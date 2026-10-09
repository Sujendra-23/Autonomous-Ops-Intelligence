# Field-service connector (Jobs and Appointments)

Mirrors an extracted action item into a field-service system as a **job** (with an **appointment**
when the task has a due date), and applies the provider's job/appointment **webhooks** back onto the
task. It is shaped like ServiceTitan's Job Planning & Management API.

> **Not run against ServiceTitan.** Real ServiceTitan access needs a partner agreement, an app key
> and a tenant, none of which this project has. Everything here was built and tested against the
> bundled mock server (`backend/mock_field_service`). The paths, header names (`ST-App-Key`) and
> field names follow ServiceTitan's general shape from memory of its public docs and are **unverified**
> against its real schema; its webhook signing scheme (if any) is also unverified, so the HMAC scheme
> below is this project's own convention, which the mock implements. Treat a first real integration
> as a porting exercise, not a config change.

## What it does

```
Task (from a meeting) ──IntegrationDispatcher──▶ FieldServiceAdapter (TaskMirror)
                                                   │ job_payload(): priority, summary, externalData, appointment window
                                                   ▼
                                        FieldServiceClient
                                          • OAuth2 client-credentials, token cached until 60 s before expiry,
                                            re-fetched once on a 401
                                          • Idempotency-Key = aoi-task-<task id>-job on every create
                                          • retries transport errors / 429 (honours Retry-After) / 5xx,
                                            same key each time, exponential backoff, max 4 attempts
                                                   ▼
                                       POST /jpm/v2/tenant/{tenant}/jobs

provider ──POST /webhooks/field-service (HMAC)──▶ apply_event() ──▶ task status + audit activity
```

* **Idempotency.** The key is derived from the task id, so a crash between "provider created the job"
  and "we saved the job id" re-sends the same key on redelivery and gets the original job back
  instead of a duplicate. Whether a real provider honours `Idempotency-Key` is provider-specific; the
  job also carries an `externalData` marker (`aoi_task_id`) for finding strays by hand.
* **Dispatcher.** Field-service mirroring is additive (like Salesforce) with its own "already
  mirrored" check, so an existing Salesforce or Linear/Jira link does not suppress it. New columns:
  `tasks.field_service_job_id` / `field_service_job_url` (migration `0006_field_service_jobs`).
* **Inbound webhook.** `POST /webhooks/field-service` (outside `/api`; the signature is the
  authentication). `X-FS-Signature: sha256=<hex HMAC-SHA256 of "<X-FS-Timestamp>.<raw body>">`, timestamp
  within 5 minutes, constant-time compare. Events: `job.completed` → `done`, `job.canceled` →
  `cancelled`, `job.hold` → `blocked`; `appointment.*` are recorded only. Each event id is recorded in a
  `TaskActivity`, so provider retries are acknowledged as `duplicate`; terminal states (`done`,
  `cancelled`) are never overwritten by a late event; unknown jobs are acknowledged (`unknown_job`)
  rather than retried forever. A secret is required (≥ 32 chars) or the route returns 503. In OIDC
  multi-tenant mode the route returns 503: mapping provider callbacks to workspaces is not built.

## Configuration

All optional; the connector is off until every required value is set (`field_service_enabled`).

```ini
FIELD_SERVICE_BASE_URL=http://127.0.0.1:9100     # https required except for localhost
FIELD_SERVICE_AUTH_URL=http://127.0.0.1:9100     # token endpoint is {AUTH_URL}/connect/token
FIELD_SERVICE_TENANT_ID=tenant-1
FIELD_SERVICE_CLIENT_ID=mock-client
FIELD_SERVICE_CLIENT_SECRET=mock-secret
FIELD_SERVICE_APP_KEY=mock-app-key
FIELD_SERVICE_WEBHOOK_SECRET=<at least 32 random characters>
# Defaults applied to every job (the provider requires them; AOI cannot infer them from a meeting):
FIELD_SERVICE_CUSTOMER_ID=11
FIELD_SERVICE_LOCATION_ID=22
FIELD_SERVICE_BUSINESS_UNIT_ID=33
FIELD_SERVICE_JOB_TYPE_ID=44
FIELD_SERVICE_CAMPAIGN_ID=              # optional
FIELD_SERVICE_APPOINTMENT_MINUTES=120   # appointment length starting at the task's due date
```

Secrets are `SecretStr`, unwrapped at the point of use; error text carries HTTP status only, never
provider bodies.

## Try it against the mock

```bash
cd backend
python -m mock_field_service                 # 127.0.0.1:9100, in-memory, MOCK_FS_PORT to change
# with the env above set in .env, ingest a transcript that yields a task: a job appears at the mock.
# Push a signed event at the app (set MOCK_FS_WEBHOOK_SECRET to the same value as FIELD_SERVICE_WEBHOOK_SECRET):
curl -s -X POST localhost:9100/_mock/events -H 'content-type: application/json' \
  -d '{"type":"job.completed","job_id":1000,"target_url":"http://localhost:8000/webhooks/field-service"}'
```

The mock imitates: token endpoint with expiry, bearer + app-key checks, `Idempotency-Key` replay
(`Idempotent-Replayed: true`; same key with a different body → 422), `POST /_mock/faults` to inject
failures (including "commit, then fail", the lost-response case), and signed webhook emission. It is
in-memory and single-process, and is not a model of ServiceTitan's behaviour.

## Tests

```bash
cd backend
pytest tests/test_field_service.py tests/test_field_service_webhook.py
```

`test_field_service.py` runs entirely in-process (no sockets, no Postgres). The webhook event tests
need Postgres (`TEST_DATABASE_URL`) and skip without it, like the other DB tests.
