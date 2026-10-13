"""Offline labeling/redaction evaluation: python -m evals.run_dlp."""

import json
from pathlib import Path

from app.services.dlp import classify, detect


def evaluate():
    cases = json.loads(Path(__file__).with_name("dlp.json").read_text())
    failures = []
    for case in cases:
        result = classify(case["text"], case["requested_label"])
        if (
            result.label != case["expected_label"]
            or result.counts != case["expected_counts"]
            or detect(result.text)
        ):
            failures.append(case["id"])
    return {"cases": len(cases), "passed": len(cases) - len(failures), "failed_case_ids": failures}


if __name__ == "__main__":
    report = evaluate()
    print(json.dumps(report, indent=2))
    raise SystemExit(bool(report["failed_case_ids"]))
