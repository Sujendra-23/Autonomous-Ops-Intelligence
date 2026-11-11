# Runbook: LLM and extraction alerts

Triage steps for the alerts in [`observability/rules.yml`](../observability/rules.yml). Each
alert's `runbook_url` points at the heading of the same name below. Metric meanings are in
[observability.md](observability.md).

Commands assume the Docker Compose stack from this repo with `make observability` running
(Prometheus on `127.0.0.1:9090`, Alertmanager on `127.0.0.1:9093`; optionally import the
provided Grafana dashboard into your own Grafana). If you set `METRICS_TOKEN`, add `-H "Authorization: Bearer $METRICS_TOKEN"` to the
`curl` calls.

## First five minutes (any alert)

1. Open the Grafana dashboard and note which `provider`, `model` and `operation` are affected.
   `operation` is `extract` (transcript extraction, including live passes) or `analytics_sql`
   (the `/api/intelligence/ask` endpoint).
2. Confirm the backend is up and scraping: `curl -s localhost:8000/health` and Prometheus
   **Status → Targets**. If the target is down, go to [AOIMetricsTargetDown](#aoimetricstargetdown):
   the other alerts are blind.
3. Look at the live counters, which are the ground truth behind every alert:

   ```sh
   curl -s localhost:8000/metrics | grep -E '^aoi_(llm_requests|extractions)_total'
   ```

4. Read recent backend logs for the event names `llm.parse_failed`, `llm.schema_violation`,
   `extraction.llm_error`, `extraction.unexpected_error`, `extraction.incremental_llm_error`:

   ```sh
   docker compose logs --since 30m backend | grep -E 'llm\.|extraction\.'
   ```

   Log lines deliberately omit error text, prompts and transcript content. The failure message
   is stored on the transcript row instead (see below).
5. Find out which transcripts failed and why:

   ```sh
   docker compose exec postgres psql -U aoi -d aoi -c \
     "SELECT id, created_at, left(error, 160) AS error FROM transcripts
      WHERE status = 'failed' ORDER BY created_at DESC LIMIT 10;"
   ```

   Errors start with `LLM error:` (provider side or unparseable output) or `Pipeline error:`
   (our code or the database).

Things to know when reading the numbers:

* Counters are per backend process and reset on restart (`make up` after a deploy shows a drop).
* `outcome="error"` counts every retry **attempt**: the Anthropic and OpenAI clients retry up to
  three times, so one failing request can add three errors.
* Prometheus' `increase()` cannot see a series' first sample: the very first error or
  invalid output after a backend start is not counted until a second one arrives. A single
  failure never pages; check the logs and the `transcripts` table if you suspect one.
* Ratio alerts need at least 3 to 5 calls in the window, so quiet periods stay silent. The
  absolute-count alert [AOIExtractionFailures](#aoiextractionfailures) covers low volume.

## AOILLMProviderErrorRatioHigh

**Severity: warning.** More than 10% of calls for a provider and operation ended in
`outcome="error"` over 30 minutes (sustained 10 minutes). The provider was reached or tried, but
the call itself failed: rate limit, timeout, connection error, 4xx/5xx, bad credentials.

**Impact.** Extractions fail with `LLM error: ...`; users see transcripts stuck in `failed`.
Analytics questions return 502.

**Triage.**

1. Is it all models or one? Dashboard, or in Prometheus:

   ```promql
   sum by (provider, model, operation) (increase(aoi_llm_requests_total{outcome="error"}[30m]))
   ```

2. Get the actual provider error from a failed transcript (step 5 above). Match it:

   | Error text contains | Likely cause | Action |
   | --- | --- | --- |
   | `401`, `authentication`, `invalid x-api-key` | Key revoked, rotated or empty | Fix `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env`, then `docker compose up -d backend worker` |
   | `429`, `rate limit`, `overloaded` | Provider throttling or capacity | Wait for the retry to clear; reduce concurrent uploads; raise the account's limits; switch `ANTHROPIC_MODEL` to a less loaded model |
   | `404`, `model not found` | Wrong or retired model id | Correct `ANTHROPIC_MODEL` / `OPENAI_MODEL` |
   | `timeout`, `connection` | Network or egress problem, or provider incident | `docker compose exec backend curl -sS -o /dev/null -w '%{http_code}\n' https://api.anthropic.com`; check the provider status page |
   | `400`, `prompt is too long` | Transcript exceeds the context window | Split the transcript; the single failing upload is not a platform fault |

3. Check whether the incident is provider-wide: the provider's status page, and whether the
   other provider's calls (if `LLM_PROVIDER` was switched) succeed.

**Mitigate.** Fix the credential/model/config and restart backend and worker. For a provider
outage, wait it out and re-run failed transcripts afterwards with
`POST /api/transcripts/{id}/reprocess` (requires `X-API-Key: $INGEST_API_KEY`; disabled in
`AUTH_MODE=oidc`). Alternatively set `LLM_PROVIDER` to the other provider if its key is
configured.

**Escalate** if errors continue after the credentials and model are confirmed good and the
provider reports no incident.

## AOILLMProviderUnavailable

**Severity: critical.** More than half of calls failed with `outcome="error"` over 10 minutes.
Treat it as a hard outage or a broken credential rather than a blip.

**Triage.** Same as [AOILLMProviderErrorRatioHigh](#aoillmprovidererrorratiohigh), but start
with the two usual causes: a key that was just rotated or exhausted (check the provider
console for billing and quota), and a provider-wide incident. Make a single test call from
inside the container so you see the raw error:

```sh
docker compose exec backend python - <<'PY'
import anthropic
from app.config import get_settings
s = get_settings()
c = anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=0)
print(c.messages.create(model=s.anthropic_model, max_tokens=8,
                        messages=[{"role": "user", "content": "ping"}]).stop_reason)
PY
```

(Use the OpenAI client and `OPENAI_API_KEY` if `LLM_PROVIDER=openai`.) The exception class and
HTTP status in the output identify the cause. Do not paste keys into tickets or chat.

**Mitigate.** As above; communicate that new meetings will queue as `failed` until fixed, and
reprocess them afterwards.

## AOILLMInvalidOutputRatioHigh

**Severity: warning.** More than 10% of responses from a model failed JSON parsing or schema
validation over 30 minutes. The provider answered fine; what came back was not usable.

**Impact.** Extractions fail with `LLM error: <provider> returned invalid JSON` or `output did
not match schema`; nothing is written for that transcript.

**Triage.**

1. Which model? The alert carries `model`. A change here usually follows a model switch
   (`ANTHROPIC_MODEL`/`OPENAI_MODEL`) or a silent provider-side model update.
2. Look at the failure kind in the logs: `llm.parse_failed` means the text was not JSON
   (truncation, prose, refusal); `llm.schema_violation` means valid JSON with the wrong shape.

   ```sh
   docker compose logs --since 1h backend | grep -E 'llm\.(parse_failed|schema_violation)'
   ```

3. Truncation is the most common cause on long meetings: extraction runs with a fixed
   `max_tokens` (4096), and a very long transcript produces output cut off mid-JSON. Compare
   `aoi_llm_tokens_total{direction="output"}` per call with that cap. If outputs sit at the
   cap, the model is running out of room.
4. Check whether failures cluster on specific transcripts (step 5 of the first five minutes) or
   spread across all of them. Clustered means the inputs are unusual; spread means the model.

**Mitigate.** Roll back to the previous known-good model id; use a larger model if `ANTHROPIC_MODEL`
is the small default; split extremely long transcripts. If the schema was changed in code
(`app/schemas/extraction.py`, `app/llm/prompts.py`), revert that change. Reprocess the failed
transcripts once the ratio recovers.

**Escalate** with two or three failing transcript ids and the model id if a model that used to
pass starts failing without a configuration change.

## AOILLMLatencyP95High

**Severity: warning.** p95 call latency for a provider and operation exceeded 30 seconds over
15 minutes (sustained 10 minutes). Latency includes output parsing, and each retry attempt is its
own observation.

**Impact.** Slow uploads and live-note passes. Analytics requests time out at 30 seconds, so
the `analytics_sql` operation starts failing near this threshold and will also raise
[AOILLMProviderErrorRatioHigh](#aoillmprovidererrorratiohigh).

**Triage.**

1. Is it one operation? Compare p95 by `operation` and `model` in Grafana. Long transcripts
   legitimately take longer; a rising p95 with stable input size points at the provider.
2. Check input and output token volume: `aoi_llm_tokens_total` per call. Large inputs or
   outputs explain latency without any fault.
3. Check retries: a burst of `outcome="error"` alongside the latency means requests are being
   retried with backoff (up to 10 seconds between attempts), which inflates the tail.
4. Check the provider status page and whether the host has CPU or network pressure
   (`docker stats --no-stream`).

**Mitigate.** If it is provider slowness, wait or switch model/provider. If inputs grew,
split transcripts or use a faster model. No change is needed for a short spike caused by one
very long meeting.

## AOILLMLatencyP95Critical

**Severity: critical.** p95 call latency exceeded 60 seconds over 15 minutes. The
`analytics_sql` operation times out at 30 seconds, so analytics is almost certainly failing;
extraction is at risk of hitting upstream gateway timeouts on uploads.

**Triage.** Follow [AOILLMLatencyP95High](#aoillmlatencyp95high), then:

1. Confirm whether requests are completing at all (`rate(aoi_llm_requests_total[5m])` by
   `outcome`). Latency with no completions means requests are hanging, not just slow.
2. Check egress from the container (`docker compose exec backend curl -sS -m 10 -o /dev/null
   -w '%{http_code} %{time_total}s\n' https://api.anthropic.com`). A slow or hanging connection
   points at the network or a proxy rather than the model.
3. Restart the backend (`docker compose restart backend worker`) only after capturing the logs,
   since counters and in-flight state reset.

**Mitigate.** Switch to another model or provider, or pause bulk uploads until it recovers.

## AOIExtractionFailures

**Severity: critical.** More than 3 full-transcript extractions failed in 15 minutes (live-mode
incremental passes are not counted). Unlike the ratio alerts this also fires at low volume.

**Triage.** Split by the `reason` label:

```promql
sum by (reason) (increase(aoi_extractions_total{mode="full",outcome="failed"}[15m]))
```

* `reason="llm_error"`: the model side failed (provider error or invalid output). Follow
  [AOILLMProviderErrorRatioHigh](#aoillmprovidererrorratiohigh) or
  [AOILLMInvalidOutputRatioHigh](#aoillminvalidoutputratiohigh) depending on which
  `aoi_llm_requests_total` outcome is rising.
* `reason="pipeline_error"`: our code or the database failed after or around the model call.
  Read the exception and the stored message:

  ```sh
  docker compose logs --since 30m backend | grep -B2 -A25 'extraction.unexpected_error'
  docker compose exec postgres psql -U aoi -d aoi -c \
    "SELECT created_at, left(error, 200) FROM transcripts
     WHERE status = 'failed' AND error LIKE 'Pipeline error%' ORDER BY created_at DESC LIMIT 5;"
  ```

  Typical causes: database unreachable or out of connections (`docker compose ps postgres`,
  `docker compose logs postgres`), a migration not applied after a deploy (`make migrate`),
  or a bug introduced by a recent release. Check what changed (`git log -5`) and roll back if
  failures began right after a deploy.

**Mitigate.** Fix the cause, then re-run each failed transcript with
`POST /api/transcripts/{id}/reprocess` (requires `X-API-Key: $INGEST_API_KEY`; the endpoint wipes
the transcript's previous extraction artifacts first so nothing is duplicated, and is disabled in
`AUTH_MODE=oidc`).

## AOIMetricsTargetDown

**Severity: critical.** Prometheus has been unable to scrape the `aoi-backend` job for 5
minutes. Every other alert in this file is blind while it fires.

**Triage.**

1. Prometheus → **Status → Targets** shows the scrape error. Common messages:
   * `connection refused` / `no such host`: the backend container is down or restarting.
     `docker compose ps backend`, then `docker compose logs --tail=100 backend`.
   * `401 Unauthorized`: `METRICS_TOKEN` is set on the backend but Prometheus has no matching
     `authorization` block in `observability/prometheus.yml`.
   * `404`: `AUTH_MODE=oidc` with no `METRICS_TOKEN`, which disables `/metrics` by design.
2. `curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/metrics` from the host separates a
   dead backend from a Prometheus-to-backend network problem.
3. If the backend restarted, its counters reset to zero; ratio alerts resume after the next
   traffic.

**Mitigate.** Restart the backend (`docker compose up -d backend`) after reading its logs, or
fix the token configuration. If the backend is healthy but unreachable from Prometheus, check
that both are on the Compose network (`docker compose -f docker-compose.yml -f compose.observability.yml --profile observability up -d`).

## Maintaining these alerts

* Rules: `observability/rules.yml`. Unit tests: `observability/tests/rules.test.yml`.
* `make check-alerts` runs `promtool check rules`, `promtool test rules`, Alertmanager
  config validation and receiver assertions for every rule; CI runs the same checks.
* `backend/tests/test_metrics.py` checks that every `aoi_*` metric named in a rule is really
  exposed, that expressions parse, and that every alert has a runbook section.
* Add a runbook section whenever you add an alert; the test fails otherwise.

## Ownership, acknowledgement and recovery

DRIs and receivers are defined in [observability.md](observability.md#dri-and-notification-routing).
The AI service on-call owns provider/latency/output incidents; platform on-call owns
extraction and scrape incidents. These roles must be assigned to the real on-call
roster before enabling production paging. Critical incidents should be acknowledged
within five minutes; warnings within one business day. If a critical incident is
unacknowledged after ten minutes, the incident-service escalation policy should
page the secondary and then the engineering lead. Alertmanager repeats alone do
not implement human acknowledgement or escalation timers.

Record impact, start/detection/mitigation/recovery timestamps, accountable DRI,
root cause and a follow-up owner. Require the resolved notification and a healthy
scrape/counter trend before closing. A reproducible local drill is available as
`make drill-metrics`; its [postmortem](drills/metrics-target.md) and JSON evidence
exercise the AOIMetricsTargetDown runbook with the real five-minute alert hold.
