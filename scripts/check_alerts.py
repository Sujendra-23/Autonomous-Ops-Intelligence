"""Validate PromQL, alert behavior, and all seven Alertmanager routes (Docker required)."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY = ROOT / "observability"


def run(image, entrypoint, *arguments):
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            entrypoint,
            "-v",
            f"{OBSERVABILITY}:/config:ro",
            image,
            *arguments,
        ],
        check=True,
    )


def main():
    prom = "prom/prometheus:v2.55.1"
    am = "prom/alertmanager:v0.28.1"
    run(prom, "/bin/promtool", "check", "rules", "/config/rules.yml")
    run(prom, "/bin/promtool", "test", "rules", "/config/tests/rules.test.yml")
    run(am, "/bin/amtool", "check-config", "/config/alertmanager.yml")
    rules = yaml.safe_load((OBSERVABILITY / "rules.yml").read_text())
    expected = {
        "AOILLMProviderErrorRatioHigh": "ai-triage",
        "AOILLMProviderUnavailable": "ai-oncall",
        "AOILLMInvalidOutputRatioHigh": "ai-triage",
        "AOILLMLatencyP95High": "ai-triage",
        "AOILLMLatencyP95Critical": "ai-oncall",
        "AOIExtractionFailures": "platform-oncall",
        "AOIMetricsTargetDown": "platform-oncall",
    }
    alerts = [rule for group in rules["groups"] for rule in group["rules"]]
    if {rule["alert"] for rule in alerts} != set(expected):
        raise ValueError(
            "Update the explicit routing expectations when changing alert rules"
        )
    for rule in alerts:
        labels = {"alertname": rule["alert"], **rule["labels"]}
        run(
            am,
            "/bin/amtool",
            "config",
            "routes",
            "test",
            "--config.file=/config/alertmanager.yml",
            "--verify.receivers=" + expected[rule["alert"]],
            *[f"{key}={value}" for key, value in labels.items()],
        )
    run(
        am,
        "/bin/amtool",
        "config",
        "routes",
        "test",
        "--config.file=/config/alertmanager.yml",
        "--verify.receivers=platform-triage",
        "alertname=UnclassifiedAlert",
    )


if __name__ == "__main__":
    main()
