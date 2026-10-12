#!/usr/bin/env python3
"""Render observability/rules.yml as a PrometheusRule for the Prometheus Operator.

The alert rules stay defined once, in observability/rules.yml (checked by promtool in CI). This
copies the same groups into deploy/k8s/monitoring/prometheusrule.yaml; a test fails if the two
drift. Run with --check to verify without writing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "observability" / "rules.yml"
OUT = ROOT / "deploy" / "k8s" / "monitoring" / "prometheusrule.yaml"
HEADER = (
    "# GENERATED from observability/rules.yml by scripts/render_prometheusrule.py. Do not edit.\n"
)


def render() -> str:
    groups = yaml.safe_load(RULES.read_text())["groups"]
    document = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {
            "name": "aoi-alerts",
            "labels": {"app.kubernetes.io/part-of": "autonomous-ops-intelligence"},
        },
        "spec": {"groups": groups},
    }
    return HEADER + yaml.safe_dump(document, sort_keys=False, width=100)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.read_text() == text else "prometheusrule.yaml is out of date")
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
