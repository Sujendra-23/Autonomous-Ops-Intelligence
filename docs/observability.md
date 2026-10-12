# LLM and extraction observability

The API exposes Prometheus metrics at `GET /metrics`, and an optional Compose profile starts
Prometheus and a provisioned Grafana dashboard.

```sh
make observability          # stack + Prometheus (127.0.0.1:9090) + Grafana (127.0.0.1:3001)
```

Grafana signs in as `admin` with `GRAFANA_ADMIN_PASSWORD`. `make observability` generates a random one in `.env` the first time. If you run `docker compose --profile observability up` yourself, set it in `.env` first.
The **AOI LLM and Extraction Observability** dashboard is provisioned automatically. `make down`
stops everything, including the profile services.

## Metrics

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `aoi_llm_requests_total` | counter | provider, model, operation, outcome | LLM calls. `outcome` is `success`, `error` (provider failure), or `invalid_output` (the response failed JSON or schema validation). |
| `aoi_llm_request_duration_seconds` | histogram | provider, model, operation | Latency of one call, including output parsing. Each retry attempt is its own observation. |
| `aoi_llm_tokens_total` | counter | provider, model, operation, direction | Tokens reported by the provider. `direction` is `input` or `output`. |
| `aoi_extractions_total` | counter | mode, outcome, reason | Extractions. `mode` is `full` (finished transcript) or `live` (incremental pass). `reason` is `none`, `llm_error`, or `pipeline_error`. |
| `aoi_ingest_document_pages_total` | counter | file_type | Pages or images rendered for vision extraction. |
| `aoi_ingest_video_frames_total` | counter | mode | Keyframes sampled from screen recordings (`scene` or `interval`). |

`operation` is `extract` for transcript extraction, `extract_document` for scanned PDFs and images,
`caption_frame` for screen-recording keyframes, and `analytics_sql` for the natural-language
analytics endpoint. Labels are low-cardinality on purpose: no tenant, transcript, or user ids.

## Securing `/metrics`

- Development: open.
- Set `METRICS_TOKEN` to require `Authorization: Bearer <token>`. Add the matching
  `authorization.credentials_file` block to `observability/prometheus.yml`.
- `AUTH_MODE=oidc` with no `METRICS_TOKEN` returns 404, so a SaaS deployment never serves metrics by accident.
  Keep the endpoint on an internal network either way.

Metrics are per process. Run a single uvicorn worker, as Compose does, or enable
`prometheus_client` multiprocess mode before scaling out.

### Scaling and per-process metrics on Kubernetes

`deploy/k8s` keeps the one-uvicorn-process-per-pod rule and scales by adding pods instead. What that
does and does not break, and what was chosen:

- **Scrape pods, not the Service.** The counters and histograms live in each process. The
  ServiceMonitor (and the `prometheus.io/*` pod annotations) make Prometheus scrape every pod's
  endpoint as its own series. Pointing a scrape at the Service ClusterIP would round-robin between
  pods and make each counter appear to jump up and down, which `increase()` reads as resets.
- **The alert rules already aggregate correctly.** They are written as
  `sum by (...) (increase(metric[window]))`: `increase()` runs per pod series, then the results are
  summed. A pod added by the autoscaler, restarted, or removed contributes what it recorded inside the
  window and nothing breaks. The existing caveat applies per pod: Prometheus cannot see a new series'
  first sample, so the first error from a brand-new pod is not counted.
- **`AOIMetricsTargetDown` fires per pod** (`up{job="aoi-backend"} == 0` for 5 minutes). A rolling
  update stays under that; a pod that stays unreachable pages. The `job` label equals the Service name,
  which is why the Service is called `aoi-backend`.
- **Only the backend autoscales, on CPU, between 2 and 4 pods, scaling down slowly.** Slow scale-in
  limits how often a pod's series disappears mid-window and gives live-transcription WebSockets time to
  drain (45 s grace period). CPU is a weak signal because requests mostly wait on the LLM provider, so
  treat the HPA as a burst valve. The cap of 4 is set by the database: each pod's pool allows 30
  connections, so more pods need a larger `max_connections` or a session-mode pooler first.
- **The worker is not autoscaled and runs one replica.** Outside SaaS mode its scheduler takes no
  lock, so a second replica would run every drift scan again and send duplicate reminders.
- **Not chosen:** `prometheus_client` multiprocess mode. It would allow `--workers` inside a pod, at the
  cost of a shared metrics directory, gauge-mode decisions and a cleanup hook on worker exit.
  More small pods give the same capacity with no code change.

## Alerts

`observability/rules.yml` ships seven alerts, loaded automatically by Prometheus. Each carries a
`runbook_url` to its section in [runbook.md](runbook.md).

| Alert | Severity | Fires when |
| --- | --- | --- |
| `AOILLMProviderErrorRatioHigh` | warning | more than 10% of calls for a provider and operation end in `outcome="error"` over 30 minutes (at least 5 calls) |
| `AOILLMProviderUnavailable` | critical | more than 50% error outcomes over 10 minutes (at least 3 calls) |
| `AOILLMInvalidOutputRatioHigh` | warning | more than 10% `invalid_output` outcomes per model over 30 minutes (at least 5 calls) |
| `AOILLMLatencyP95High` | warning | p95 call latency above 30 s over 15 minutes |
| `AOILLMLatencyP95Critical` | critical | p95 call latency above 60 s over 15 minutes |
| `AOIExtractionFailures` | critical | more than 3 full extractions failed in 15 minutes |
| `AOIMetricsTargetDown` | critical | the `aoi-backend` scrape target is down for 5 minutes |

Ratios use `increase()` over a window plus a minimum call count rather than a request-rate
floor, so a deployment that handles a few meetings a day can still alert. One caveat: Prometheus
cannot see the first sample of a counter series, so the first error after a backend start is
not counted until a second arrives.

```sh
make check-alerts      # promtool check rules + promtool test rules (needs Docker)
```

On Kubernetes the same rules ship as a `PrometheusRule` generated from this file
(`scripts/render_prometheusrule.py`), so there is still one definition; see
[kubernetes.md](kubernetes.md).

`observability/tests/rules.test.yml` feeds synthetic series to every alert and asserts both that
it fires with the expected labels and annotations and that near-misses (healthy traffic, too few
calls, live-mode failures) stay silent. CI runs the same commands.

## Checks

`backend/tests/test_metrics.py` records calls through the real Anthropic and OpenAI client code paths
with stubbed SDKs, and checks that every metric named in the dashboard and alert rules is actually
exposed, parses as PromQL, and that every alert has a `runbook_url` that resolves to a section
in `docs/runbook.md`.
