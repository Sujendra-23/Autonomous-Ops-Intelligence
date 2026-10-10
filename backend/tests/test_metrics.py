"""LLM and extraction metrics, the /metrics endpoint, and the Grafana/alert assets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
import yaml
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, generate_latest
from pydantic import SecretStr

from app import main as app_main
from app.llm.client import AnthropicClient, LLMError, OpenAIClient
from app.metrics import record_extraction, track_llm_call

OBSERVABILITY = Path(__file__).resolve().parents[2] / "observability"

VALID = (
    '{"summary": "s", "project_hint": null, "tasks": [], "decisions": [], '
    '"risks": [], "blockers": []}'
)


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def make_anthropic(create) -> AnthropicClient:
    client = AnthropicClient.__new__(AnthropicClient)
    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    client._model = "test-model-a"
    return client


def make_openai(create) -> OpenAIClient:
    client = OpenAIClient.__new__(OpenAIClient)
    completions = SimpleNamespace(create=create)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client._model = "test-model-o"
    return client


def anthropic_labels(outcome: str) -> dict[str, str]:
    return {
        "provider": "anthropic",
        "model": "test-model-a",
        "operation": "extract",
        "outcome": outcome,
    }


async def test_anthropic_success_records_calls_latency_and_tokens() -> None:
    async def create(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            content=[SimpleNamespace(text=VALID)],
            usage=SimpleNamespace(input_tokens=120, output_tokens=45),
        )

    base = {"provider": "anthropic", "model": "test-model-a", "operation": "extract"}
    calls = sample("aoi_llm_requests_total", **anthropic_labels("success"))
    latency = sample("aoi_llm_request_duration_seconds_count", **base)
    tokens_in = sample("aoi_llm_tokens_total", **base, direction="input")
    tokens_out = sample("aoi_llm_tokens_total", **base, direction="output")

    await make_anthropic(create).extract("transcript")

    assert sample("aoi_llm_requests_total", **anthropic_labels("success")) == calls + 1
    assert sample("aoi_llm_request_duration_seconds_count", **base) == latency + 1
    assert sample("aoi_llm_tokens_total", **base, direction="input") == tokens_in + 120
    assert sample("aoi_llm_tokens_total", **base, direction="output") == tokens_out + 45


async def test_anthropic_invalid_output_counts_tokens_and_failure() -> None:
    async def create(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            content=[SimpleNamespace(text="not json at all")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=3),
        )

    invalid = sample("aoi_llm_requests_total", **anthropic_labels("invalid_output"))
    success = sample("aoi_llm_requests_total", **anthropic_labels("success"))

    with pytest.raises(LLMError):
        await make_anthropic(create).extract("transcript")

    assert sample("aoi_llm_requests_total", **anthropic_labels("invalid_output")) == invalid + 1
    assert sample("aoi_llm_requests_total", **anthropic_labels("success")) == success


async def test_anthropic_provider_error_counts_as_error() -> None:
    async def create(**_: object) -> SimpleNamespace:
        raise anthropic.AnthropicError("provider down")

    errors = sample("aoi_llm_requests_total", **anthropic_labels("error"))

    with pytest.raises(LLMError):
        await make_anthropic(create).extract("transcript")

    assert sample("aoi_llm_requests_total", **anthropic_labels("error")) == errors + 1


async def test_openai_records_prompt_and_completion_tokens() -> None:
    async def create(**_: object) -> SimpleNamespace:
        message = SimpleNamespace(content=VALID)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=80),
        )

    base = {"provider": "openai", "model": "test-model-o", "operation": "extract"}
    tokens_in = sample("aoi_llm_tokens_total", **base, direction="input")
    tokens_out = sample("aoi_llm_tokens_total", **base, direction="output")
    calls = sample("aoi_llm_requests_total", **base, outcome="success")

    await make_openai(create).extract("transcript")

    assert sample("aoi_llm_requests_total", **base, outcome="success") == calls + 1
    assert sample("aoi_llm_tokens_total", **base, direction="input") == tokens_in + 200
    assert sample("aoi_llm_tokens_total", **base, direction="output") == tokens_out + 80


def test_track_llm_call_without_usage_records_no_tokens() -> None:
    base = {"provider": "p", "model": "m", "operation": "op"}
    with track_llm_call("p", "m", "op") as call:
        call.record_usage(None, 0)
    assert sample("aoi_llm_tokens_total", **base, direction="input") == 0
    assert sample("aoi_llm_requests_total", **base, outcome="success") >= 1


def test_record_extraction_labels() -> None:
    labels = {"mode": "full", "outcome": "failed", "reason": "llm_error"}
    before = sample("aoi_extractions_total", **labels)
    record_extraction("failed", "llm_error")
    assert sample("aoi_extractions_total", **labels) == before + 1


def settings(token: str = "", auth_mode: str = "development") -> SimpleNamespace:
    return SimpleNamespace(metrics_token=SecretStr(token), auth_mode=auth_mode)


def test_metrics_endpoint_is_open_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_main, "get_settings", lambda: settings())
    record_extraction("completed")
    response = TestClient(app_main.app).get("/metrics")
    assert response.status_code == 200
    assert "aoi_extractions_total" in response.text


def test_metrics_endpoint_requires_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_main, "get_settings", lambda: settings("s3cret"))
    client = TestClient(app_main.app)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_metrics_endpoint_fails_closed_in_saas_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_main, "get_settings", lambda: settings(auth_mode="oidc"))
    assert TestClient(app_main.app).get("/metrics").status_code == 404


# --------------------------------------------------------------------------- #
# Dashboard and alert rules must only reference metrics the code really emits  #
# --------------------------------------------------------------------------- #


def exposed_metric_names() -> set[str]:
    # Touch every metric family so each exposes at least one series.
    with track_llm_call("probe", "probe", "probe") as call:
        call.record_usage(1, 1)
    record_extraction("completed", mode="probe")
    return set(re.findall(r"^(aoi_\w+?)(?:\{| )", generate_latest().decode(), re.M))


def dashboard_exprs() -> list[str]:
    path = OBSERVABILITY / "grafana" / "dashboards" / "aoi-llm-observability.json"
    dashboard = json.loads(path.read_text())
    return [t["expr"] for panel in dashboard["panels"] for t in panel["targets"]]


def rule_exprs() -> list[str]:
    rules = yaml.safe_load((OBSERVABILITY / "rules.yml").read_text())
    return [rule["expr"] for group in rules["groups"] for rule in group["rules"]]


def test_dashboard_and_rules_reference_only_exposed_metrics() -> None:
    exposed = exposed_metric_names()
    exprs = dashboard_exprs() + rule_exprs()
    assert exprs
    for expr in exprs:
        for name in re.findall(r"\baoi_\w+", expr):
            assert name in exposed, f"{name} in {expr!r} is not exposed by the app"


def test_dashboard_and_rules_are_valid_promql() -> None:
    promql = pytest.importorskip("promql_parser")
    for expr in dashboard_exprs() + rule_exprs():
        promql.parse(expr.replace("$__rate_interval", "5m"))


def test_prometheus_config_scrapes_backend_metrics() -> None:
    config = yaml.safe_load((OBSERVABILITY / "prometheus.yml").read_text())
    job = config["scrape_configs"][0]
    assert job["metrics_path"] == "/metrics"
    assert job["static_configs"][0]["targets"] == ["backend:8000"]
