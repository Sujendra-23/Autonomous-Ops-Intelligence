"""Stop an isolated metrics target, observe routed firing/resolved webhooks, report.

Uses the real AOIMetricsTargetDown rule with its unchanged five-minute hold.
No alerts are injected into Alertmanager, and no application containers stop.
"""

import argparse
import json
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(UTC).isoformat()


def fetch(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_until(check, timeout=540):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (URLError, TimeoutError, IndexError, KeyError):
            pass
        time.sleep(3)
    raise TimeoutError("Drill did not observe the expected state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/drills/metrics-target"
    )
    args = parser.parse_args()
    project = "aoi-drill-" + uuid.uuid4().hex[:10]
    command = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(ROOT / "observability/compose.drill.yml"),
    ]
    evidence = {
        "started_at": now(),
        "project": project,
        "alert": "AOIMetricsTargetDown",
        "rule_hold": "5m",
        "events": [],
    }

    def compose(*arguments):
        return subprocess.check_output([*command, *arguments], text=True).strip()

    def address(service, port):
        return "http://" + compose("port", service, str(port))

    try:
        print(
            "Starting isolated Prometheus, Alertmanager, receiver and metrics target",
            flush=True,
        )
        compose("up", "-d", "--wait", "--wait-timeout", "60")
        prometheus = address("prometheus", 9090)
        receiver = address("alert-receiver", 8080)
        wait_until(
            lambda: fetch(
                prometheus + "/api/v1/query?query=up%7Bjob%3D%22aoi-backend%22%7D"
            )["data"]["result"][0]["value"][1]
            == "1",
            timeout=60,
        )
        evidence["healthy_at"] = now()
        compose("stop", "backend")
        evidence["fault_injected_at"] = now()
        print(
            "Target stopped; waiting for the real five-minute alert and platform-oncall webhook",
            flush=True,
        )

        def notification(status):
            for event in fetch(receiver + "/events"):
                payload = event["payload"]
                if (
                    payload["status"] == status
                    and payload["receiver"] == "platform-oncall"
                    and event["path"] == "/platform-oncall"
                    and any(
                        a["labels"].get("alertname") == evidence["alert"]
                        for a in payload["alerts"]
                    )
                ):
                    return event
            return None

        evidence["firing"] = wait_until(lambda: notification("firing"))
        evidence["firing_observed_at"] = now()
        print(
            "Firing webhook received; following runbook mitigation: restart target",
            flush=True,
        )
        compose("start", "backend")
        evidence["mitigated_at"] = now()
        evidence["resolved"] = wait_until(lambda: notification("resolved"), timeout=120)
        evidence["resolved_observed_at"] = now()
        evidence["events"] = fetch(receiver + "/events")
        evidence["result"] = "passed"
    except Exception as exc:
        evidence["result"] = "failed"
        evidence["failure_type"] = type(exc).__name__
        raise
    finally:
        evidence["finished_at"] = now()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        if evidence["result"] == "passed":
            runbook = "../runbook.md#aoimetricstargetdown"
            args.output.with_suffix(".md").write_text(
                f"# Metrics target fault-injection postmortem\n\n"
                f"Result: **passed**. Started {evidence['started_at']} (UTC).\n\n"
                f"Impact: isolated test scrape target unavailable; no application traffic affected.\n\n"
                f"DRI: platform-oncall. Trigger: stopped the drill backend at "
                f"{evidence['fault_injected_at']}. Prometheus evaluated the unchanged five-minute "
                f"AOIMetricsTargetDown rule. Alertmanager delivered the firing webhook at "
                f"{evidence['firing']['received_at']} to /platform-oncall.\n\n"
                f"Runbook response: [{evidence['alert']}]({runbook}) — confirm the target is down, "
                f"restart it, verify scrape recovery. Restarted at {evidence['mitigated_at']}; "
                f"resolved webhook received at {evidence['resolved']['received_at']}.\n\n"
                f"Cause: deliberate target shutdown. Detection and routing worked; mitigation "
                f"cleared the alert. Evidence: [{args.output.name}.json]({args.output.name}.json).\n\n"
                f"Follow-up (platform-oncall): configure real incident receiver URLs and confirm "
                f"delivery in staging before enabling production paging. This drill validates "
                f"the local webhook path; it does not exercise the production backend or a human pager.\n"
            )
        subprocess.run([*command, "down", "-v", "--remove-orphans"], check=False)
    print(
        "Drill passed; evidence and postmortem written to " + str(args.output),
        flush=True,
    )


if __name__ == "__main__":
    main()
