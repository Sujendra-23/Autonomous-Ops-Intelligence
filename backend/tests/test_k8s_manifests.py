"""Static checks on deploy/k8s that need no cluster (kubectl/kind checks run in CI).

These pin the decisions that are easy to break silently: probes, the single-process backend,
the worker not being autoscaled, no real Secret in the tree, and the PrometheusRule staying in
sync with observability/rules.yml.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "deploy" / "k8s"


def load(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def objects(kind: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in K8S.rglob("*.yaml"):
        if path.name in {"kustomization.yaml", "secret.example.yaml"}:
            continue
        for doc in load(path):
            if doc["kind"] == kind:
                found[doc["metadata"]["name"]] = doc
    return found


def container(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def test_backend_probes_split_liveness_from_readiness() -> None:
    backend = container(objects("Deployment")["aoi-backend"])
    assert backend["livenessProbe"]["httpGet"]["path"] == "/health"
    assert backend["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert backend["startupProbe"]["httpGet"]["path"] == "/health"


@pytest.mark.parametrize("name", ["aoi-backend", "aoi-worker"])
def test_pods_are_locked_down(name: str) -> None:
    deployment = objects("Deployment")[name]
    spec = deployment["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["runAsUser"] == 10001
    assert spec["automountServiceAccountToken"] is False
    security = container(deployment)["securityContext"]
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"]["drop"] == ["ALL"]


def test_backend_stays_a_single_uvicorn_process_per_pod() -> None:
    backend = container(objects("Deployment")["aoi-backend"])
    assert "command" not in backend and "args" not in backend  # the image CMD, no --workers
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    assert "--workers" not in dockerfile


def test_worker_is_one_replica_recreate_and_not_autoscaled() -> None:
    worker = objects("Deployment")["aoi-worker"]
    assert worker["spec"]["replicas"] == 1
    assert worker["spec"]["strategy"]["type"] == "Recreate"
    targets = {
        h["spec"]["scaleTargetRef"]["name"] for h in objects("HorizontalPodAutoscaler").values()
    }
    assert targets == {"aoi-backend"}


def test_hpa_cap_respects_the_database_connection_budget() -> None:
    hpa = objects("HorizontalPodAutoscaler")["aoi-backend"]["spec"]
    pool = 10 + 20  # pool_size + max_overflow in app/database.py
    assert hpa["maxReplicas"] * pool <= 200  # kind overlay sets max_connections=200
    assert hpa["behavior"]["scaleDown"]["stabilizationWindowSeconds"] >= 300


def test_config_is_fail_closed_by_default() -> None:
    data = objects("ConfigMap")["aoi-config"]["data"]
    assert (data["ENVIRONMENT"], data["AUTH_MODE"]) == ("production", "oidc")
    assert not any("KEY" in key or "TOKEN" in key or "PASSWORD" in key for key in data)


def test_no_real_secret_is_committed() -> None:
    for path in K8S.rglob("*.yaml"):
        for doc in load(path):
            if doc.get("kind") == "Secret":
                assert path.name == "secret.example.yaml", path
                values = [*doc.get("stringData", {}).values(), *doc.get("data", {}).values()]
                assert all(v in ("", None) or "CHANGE_ME" in str(v) for v in values), path


def test_service_monitor_targets_the_backend_service_and_job_label() -> None:
    service = objects("Service")["aoi-backend"]
    monitor = objects("ServiceMonitor")["aoi-backend"]["spec"]
    for key, value in monitor["selector"]["matchLabels"].items():
        assert service["metadata"]["labels"][key] == value
    assert monitor["endpoints"][0]["port"] in {p["name"] for p in service["spec"]["ports"]}
    # job label defaults to the Service name; observability/rules.yml alerts on this exact value.
    rules = (ROOT / "observability" / "rules.yml").read_text()
    assert f'job="{service["metadata"]["name"]}"' in rules


def test_prometheus_rule_matches_observability_rules() -> None:
    spec = importlib.util.spec_from_file_location(
        "render_prometheusrule", ROOT / "scripts" / "render_prometheusrule.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (K8S / "monitoring" / "prometheusrule.yaml").read_text() == module.render()
    alerts = [
        rule["alert"]
        for group in objects("PrometheusRule")["aoi-alerts"]["spec"]["groups"]
        for rule in group["rules"]
    ]
    assert len(alerts) == 7
