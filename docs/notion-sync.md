# Notion task sync

AOI mirrors each new task into a `to_do` checkbox beneath its Notion project page,
independently of Linear/Jira. `tasks.notion_block_id` identifies the exact block;
the visible `[AOI:<task UUID>]` marker lets a retry recover an append whose response
or database commit was lost. Keep the marker until a mapping is stored. The
dispatcher locks task rows to serialize concurrent mirror creation.

## Enable polling

1. Run `alembic upgrade head` from `backend/` using schema-owner database
   credentials. Migration `0007_notion_sync` adds independent Notion reconciliation
   fields and a unique workspace/block index.
2. Give the Notion integration read, insert, and update content capabilities, and
   share a parent **page** with it. For local development, configure
   `NOTION_API_KEY`, `NOTION_PARENT_PAGE_ID`, and `NOTION_SYNC_ENABLED=true`.
3. In OIDC deployments, an owner/admin saves encrypted workspace settings via
   `PUT /api/account/connectors` instead of global environment credentials:

   ```json
   {
     "values": {
       "notion_api_key": "<integration token>",
       "notion_parent_page_id": "<parent page UUID>",
       "notion_sync_enabled": true
     }
   }
   ```

4. Run the API and `python -m app.workers.scheduler`. New extractions create task
   checkboxes. Polling reconciles up to 100 tasks per workspace batch, oldest first,
   at `INTEGRATION_INTERVAL_SECONDS` (60 seconds by default).

Checking a mapped Notion task marks the AOI task `done`; unchecking a completed
task reopens it. Unchecked blocks preserve `in_progress`, `blocked`, and
`cancelled`. AOI changes push `done` as checked and every other status as unchecked.
This represents completion, not a full Notion database status property.

Pending AOI edits win until successfully pushed. Otherwise the current Notion block
is authoritative. Reconciliation locks rows, records activity for actual changes,
and emits `task.updated` through the configured outbox. If a task also has a
Linear/Jira mirror, an incoming provider change queues the other mirror for update.
When both providers changed between polls, the Linear/Jira scan runs first.
Failures preserve pending work and populate `notion_sync_error`; later polls retry.
The task API exposes the mapping, pending state, last check time, and safe error text.

Existing summary bullets have no stable block mapping and are not guessed by title.
They remain historical content. Checkboxes are created for tasks when the updated
dispatcher publishes a transcript; existing mapped checkboxes are reused.

## Optional TypeScript webhook receiver

`services/notion-webhook/src/handler.ts` exports a Fetch `Request` → `Response`
handler. `server.ts` is its Node 22 HTTP adapter. There are no runtime npm dependencies.
Polling remains enabled to recover missed deliveries, busy rows, and larger batches.

```sh
cd services/notion-webhook
npm ci
npm test
cp .env.example .env
# Fill in configuration, then:
npm run build
node --env-file=.env dist/server.js
```

Expose `POST /webhooks/notion` through an HTTPS reverse proxy. `AOI_BASE_URL` must
be an HTTPS origin; HTTP is allowed only for localhost, 127.0.0.1, or the internal
Compose hostname `backend`. An optional container image is available:

```sh
docker build -t aoi-notion-webhook services/notion-webhook
docker run --rm --env-file services/notion-webhook/.env -p 127.0.0.1:8787:8787 aoi-notion-webhook
```

Create a workspace-bound AOI token with `POST /api/account/tokens`, owned by a
member/admin/owner, and set `AOI_TOKEN` and `AOI_WORKSPACE_ID`. Set
`NOTION_WORKSPACE_ID` and `NOTION_INTEGRATION_ID` for that Notion integration.
Use one receiver configuration per workspace/integration binding. AOI revalidates
membership and token expiry on every forwarded request. Rotate the token before
it expires (at most 90 days). For trusted local development without OIDC, set
`AOI_TOKEN` to the local `INGEST_API_KEY`; it is sent as `X-API-Key` as well.

For Notion subscription activation:

1. Leave `NOTION_WEBHOOK_VERIFICATION_TOKEN` empty and set
   `NOTION_WEBHOOK_SETUP_FILE` to a new absolute path in a private directory.
   Restrict this setup listener to the activation window.
2. Add the HTTPS webhook URL in the Notion integration's Webhooks tab and subscribe
   to `page.content_updated`. Notion sends an unsigned `verification_token` once.
   The server writes it with mode `0600`, refusing to overwrite an existing file.
   It never prints or echoes the token. Setup mode never forwards events.
3. Read the file privately, paste its token into Notion's verification UI, and set
   the same value as `NOTION_WEBHOOK_VERIFICATION_TOKEN`. Unset the setup file option,
   restart the receiver, and remove the setup token file. Never commit either token.
   The nonroot Docker image needs a private writable mount for setup.

Active mode verifies `X-Notion-Signature` as HMAC-SHA256 over the original bytes,
checks the configured Notion workspace/integration IDs, validates the page UUID,
and forwards only that page ID to `POST /api/integrations/notion/reconcile`.
AOI's normal authentication and workspace isolation apply to this endpoint.
It schedules up to 500 mapped tasks on that page for the next worker tick and
returns `{"scheduled": N}` without provider calls. Unknown pages schedule nothing.
The public event cannot specify an AOI task, workspace, role, or task status.
Replayed events schedule another read of current state; unchanged statuses create
no duplicate activity. Forwarding failures return HTTP 503 for Notion retries.
Bodies are limited to 128 KiB, and redirects are refused.

## Permission boundary

| AOI role | Read tasks | Trigger Notion writes/sync | Configure connector |
| --- | --- | --- | --- |
| Owner/admin | Yes | Yes | Yes |
| Member | Yes | Yes | No |
| Viewer | Yes | No | No |

OIDC mode uses encrypted workspace connector credentials without falling back to
global Notion tokens. The adapter requires a workspace context and a writable
principal, or explicit workspace-worker authority. It checks each resource's AOI
workspace and the live Notion project parent and task-block parent before updates.
Moved, archived, deleted, inaccessible, or non-checkbox blocks fail closed.
Changing the configured parent pauses old mappings until deliberately moved back
or remapped.

These checks enforce AOI roles around a shared Notion integration. Notion still
authorizes the integration's token; this does not impersonate each AOI user or
establish their personal Notion page permissions. Share only the intended parent
page with the integration. Development mode retains the trusted local API-key
behavior; use OIDC for per-user role enforcement.

Provider references: [Notion webhooks](https://developers.notion.com/reference/webhooks),
[retrieve a block](https://developers.notion.com/reference/retrieve-a-block), and
[connection capabilities](https://developers.notion.com/reference/capabilities).
