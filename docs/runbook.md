# Operations runbook

For whoever is on call. It covers what to look at first, how to read the webhook outbox alert,
and how to write up what happened. Commands assume the Docker Compose stack from `make up`;
add `-f compose.production.yml` where your deployment uses it. API calls need `X-API-Key` when
`INGEST_API_KEY` is set. See [production-release.md](production-release.md) for deployment setup.

## First five minutes

1. **Is the API up?** `curl localhost:8000/health` returns `{"status":"ok",...}`.
2. **Is the worker alive?** `docker compose logs --tail 50 worker`. A healthy worker logs
   `scheduler.tick` every `MONITOR_INTERVAL_SECONDS` (default 900) and nothing at error level.
   The worker delivers webhooks, syncs Linear and Jira, and extracts queued transcripts, so a dead
   worker causes most other symptoms.
3. **Which integrations are on?** `GET /api/integrations/status`.
4. **Is the outbox healthy?** `GET /api/integrations/webhooks/health`. An empty `problems` list
   means yes.
5. **Anything stuck?** Run the queries in [Useful queries](#useful-queries).

Log events worth searching for: `outbox.unhealthy`, `outbox.recovered`,
`scheduler.integration_failed`, `scheduler.extraction_failed`, `scheduler.tick_failed`,
`scheduler.workspace_operation_failed`.

## Webhook outbox

The outbox is the `webhook_deliveries` table. The worker sends each row to `WEBHOOK_URL`, signed,
with up to 8 attempts and exponential backoff (doubling from one minute, capped at one hour). 408, 429, 5xx
and network errors retry. Any other 4xx marks the row `failed` straight away.

### The alert

On every worker tick the outbox is checked. When it is unhealthy the worker logs `outbox.unhealthy`
and, if Slack is configured, posts to `SLACK_DEFAULT_CHANNEL`. The alert repeats at most once per
cooldown and a single "recovered" message follows when the outbox is clean. The cooldown is held in
worker memory, so a restart can repeat an open alert.

| Setting | Default | Alert fires when |
| --- | --- | --- |
| `OUTBOX_ALERT_FAILED_THRESHOLD` | `1` | that many deliveries are `failed` |
| `OUTBOX_ALERT_STALE_SECONDS` | `900` | any `pending` delivery is overdue by more than this |
| `OUTBOX_ALERT_COOLDOWN_SECONDS` | `3600` | minimum gap between repeat alerts |

Nothing is checked while `WEBHOOK_URL` is unset. The same numbers are available any time from
`GET /api/integrations/webhooks/health` (`pending`, `failed`, `overdue`, `oldest_overdue_seconds`,
`problems`).

### "N failed webhook deliveries need a manual retry"

Failed rows never retry on their own.

1. List them: `GET /api/integrations/webhooks/deliveries?limit=200`. Read `last_error`:
   - `HTTP 401` or `HTTP 403`: the receiver rejects the signature. Check that its secret equals
     `WEBHOOK_SECRET` (the receiver must hash the raw body, not re-serialized JSON) and that its
     clock is within 5 minutes. A working receiver is in
     [examples/webhook-receivers](../examples/webhook-receivers/).
   - `HTTP 404` or `HTTP 410`: the URL changed or the workflow was deleted. Fix `WEBHOOK_URL`.
   - `HTTP 400`, `HTTP 422`: the receiver does not accept the payload. Compare against the event
     format in [integrations.md](integrations.md#outbound-webhooks).
   - `Network error` after 8 attempts: the receiver was unreachable for about two hours across the 8 attempts.
   - `Destination changed; restore configuration before retrying`: `WEBHOOK_URL` was edited after
     the event was queued. Restore the original URL to retry, or leave these rows failed on purpose.
2. Fix the cause first. Retrying into a receiver that still rejects events only burns attempts.
3. Requeue each row: `POST /api/integrations/webhooks/deliveries/{id}/retry`. The event ID is
   kept, so a receiver that deduplicates on `X-AOI-Event-ID` handles it once.
4. Watch `GET /api/integrations/webhooks/health` until `failed` is `0`.

### "N pending deliveries are overdue"

Pending rows should be picked up within one worker interval. Overdue rows mean the worker is not
delivering.

1. `docker compose ps worker`. If it is down, `docker compose up -d worker`.
2. `docker compose logs --tail 200 worker`. Look for `scheduler.integration_failed` (an exception
   inside delivery or sync) or a crash loop.
3. Check the database connection and `WEBHOOK_SECRET`. With `WEBHOOK_URL` set, a secret shorter
   than 32 characters stops the settings from loading at all.
4. If the worker is healthy but behind, deliveries are slow. A receiver that takes close to the
   10 s timeout limits throughput to about 50 rows per tick. Fix or scale the receiver.
5. After recovery the backlog drains without any manual retry. Confirm `overdue` goes to `0`.

## Task sync errors (Linear and Jira)

Failed pushes keep the local status and set `sync_error` on the task.

```sql
SELECT id, title, status, sync_error, sync_checked_at FROM tasks
WHERE sync_error IS NOT NULL ORDER BY sync_checked_at DESC;
```

Typical causes are a status ID missing from `LINEAR_STATUS_MAP` or `JIRA_STATUS_MAP`, a Jira
transition that needs extra fields, and an expired API token. Fix the configuration or token. The
worker retries pending tasks on its next pass, so there is nothing to requeue.

## Transcripts stuck in processing

`chunking` or `extracting` for more than a few minutes, or `failed`:

```sql
SELECT id, title, status, error, updated_at FROM transcripts
WHERE status IN ('received','chunking','extracting','failed') ORDER BY updated_at DESC LIMIT 20;
```

The worker picks up `received` transcripts and resumes non-live ones. For `failed`, read `error`.
Reprocessing (`POST /api/transcripts/{id}/reprocess`) creates tasks again and can duplicate issues
in Linear, Jira, Notion and Salesforce, so check the target systems first. In the hosted (OIDC)
mode reprocessing is disabled for that reason.

## Useful queries

Open a shell with `docker compose exec postgres psql -U aoi -d aoi` (use your own user and database).

```sql
-- Outbox by state
SELECT status, count(*), min(next_attempt_at) AS oldest_due FROM webhook_deliveries GROUP BY status;

-- Why deliveries failed
SELECT last_error, count(*) FROM webhook_deliveries WHERE status = 'failed' GROUP BY 1 ORDER BY 2 DESC;

-- Pending rows that are overdue (what the alert counts)
SELECT id, event_type, attempts, next_attempt_at FROM webhook_deliveries
WHERE status = 'pending' AND next_attempt_at < now() - interval '15 minutes' ORDER BY next_attempt_at;
```

## Escalate when

- The worker crash-loops after a restart or a deploy.
- Failed deliveries keep returning after the receiver is confirmed healthy.
- Any sign that a signing secret or API key was exposed. Rotate it, then coordinate the new
  `WEBHOOK_SECRET` with the receiver, because rotation is not automatic.

## After an incident

Write it down while it is fresh, in your issue tracker or team wiki:

- **Impact:** which events were delayed or lost, for how long, and which receivers were affected.
- **Timeline:** when the alert fired, who acknowledged it, what was tried, when `problems` went empty.
- **Root cause:** the `last_error` or log event that explains it, not just the symptom.
- **Fix:** the configuration or code change, and how you confirmed it (health endpoint, a test event).
- **Follow-up:** what would have caught this sooner, and who owns it.
