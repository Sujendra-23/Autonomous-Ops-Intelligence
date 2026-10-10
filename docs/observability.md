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

`operation` is `extract` for transcript extraction and `analytics_sql` for the natural-language
analytics endpoint. Labels are low-cardinality on purpose: no tenant, transcript, or user ids.

## Securing `/metrics`

- Development: open.
- Set `METRICS_TOKEN` to require `Authorization: Bearer <token>`. Add the matching
  `authorization.credentials_file` block to `observability/prometheus.yml`.
- `AUTH_MODE=oidc` with no `METRICS_TOKEN` returns 404, so a SaaS deployment never serves metrics by accident.
  Keep the endpoint on an internal network either way.

Metrics are per process. Run a single uvicorn worker, as Compose does, or enable
`prometheus_client` multiprocess mode before scaling out.

## Alerts

`observability/rules.yml` ships three starter alerts: LLM failure ratio above 10%, p95 latency above
30 seconds, and repeated failed full extractions. Prometheus loads them automatically.

## Checks

`backend/tests/test_metrics.py` records calls through the real Anthropic and OpenAI client code paths
with stubbed SDKs, and checks that every metric named in the dashboard and alert rules is actually
exposed and parses as PromQL.
