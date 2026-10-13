# Metrics target fault-injection postmortem

Result: **passed**. Started 2026-10-13T17:26:47.866151+00:00 (UTC).

Impact: isolated test scrape target unavailable; no application traffic affected.

DRI: platform-oncall. Trigger: stopped the drill backend at 2026-10-13T17:27:07.525730+00:00. Prometheus evaluated the unchanged five-minute AOIMetricsTargetDown rule. Alertmanager delivered the firing webhook at 2026-10-13T17:32:37.215356+00:00 to /platform-oncall.

Runbook response: [AOIMetricsTargetDown](../runbook.md#aoimetricstargetdown) — confirm the target is down, restart it, verify scrape recovery. Restarted at 2026-10-13T17:32:38.895208+00:00; resolved webhook received at 2026-10-13T17:33:07.217798+00:00.

Cause: deliberate target shutdown. Detection and routing worked; mitigation cleared the alert. Evidence: [metrics-target.json](metrics-target.json).

Follow-up (platform-oncall): configure real incident receiver URLs and confirm delivery in staging before enabling production paging. This drill validates the local webhook path; it does not exercise the production backend or a human pager.
