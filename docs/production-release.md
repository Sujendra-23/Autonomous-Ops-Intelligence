# Public SaaS release preparation

No hosting or customer accounts were created by this change. The application now supports verified accounts, separate workspaces, roles, workspace-bound extension tokens, encrypted connector settings and database-enforced isolation. Hosting remains an explicit later step.

## Identity and configuration

Use a standards-compliant OIDC provider with a public browser client using **Authorization Code + PKCE**, signed RS256/ES256 access tokens, and an API audience. Configure the exact issuer, audience and HTTPS JWKS endpoint. Register `https://YOUR-APP-DOMAIN/auth/callback` and the app origin as allowed logout/redirect URLs. Enable verified signup and account recovery at the provider. Passwords are never stored in this application. Access-token expiry returns the browser to sign-in; tokens are not silently renewed.

Copy `.env.production.example` to `.env.production`, set mode 0600, and replace placeholders. Browser client IDs are public; never put client secrets, API keys or connector credentials in NEXT_PUBLIC_* variables. The API rejects production/staging with development authentication. Set platform LLM/STT keys and explicit HTTPS CORS origins. A Redis outage blocks authenticated requests rather than allowing unlimited paid work.

Generate `CONNECTOR_ENCRYPTION_KEY` with `Fernet.generate_key()`. Store this key separately from database backups; losing it makes connectors unreadable. Rotate by decrypting/re-encrypting workspace ciphertext in a maintenance window before replacing the key. Restart API/worker processes after configuration changes.

The first verified login gets a new private workspace. Historical rows are quarantined under `00000000-0000-0000-0000-000000000001` with **no memberships**. Never automatically give historical data to a new signup. An operator may deliberately migrate verified legacy ownership during a maintenance window.

## Database and isolation

Use managed PostgreSQL 17 or newer with pgvector, uuid-ossp and pg_trgm. Apply migrations using a separate schema-owner credential. Run API and worker using a `NOSUPERUSER NOBYPASSRLS` role. The API checks these attributes and forced row security at startup. Do not use transaction-pooling middleware that discards session advisory locks for the worker; use a direct connection or session pooling.

Provision the runtime role through your database administration tooling, storing the password in its secret manager. After migration, as the schema owner:

```sql
GRANT USAGE ON SCHEMA public TO aoi_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aoi_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aoi_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA analytics_masked, analytics_owner FROM aoi_runtime;
```

Do not grant the runtime role membership in the schema-owner role. Keep PostgreSQL and Redis private. Tenant tables use forced row-level policies for reads and writes, ORM filters, and composite foreign keys that prevent references across workspaces. Sessions set workspace context transaction-locally, including after commits. Customer-supplied IDs are never sufficient authorization.

## Customer workflows

In **Workspace & connectors**, owners/admins configure Discord, Microsoft Teams, Slack, Notion, Linear, Jira Cloud and Google Calendar. Credentials are encrypted at rest and never returned by status APIs. Workspace settings never inherit deployment connector credentials. These are manual credential connections; automatic OAuth consent for connectors is not implemented.

Owners add existing account IDs as admin/member/viewer; admins can manage members/viewers only. Owners cannot be removed or downgraded through these endpoints. Account IDs are shown to the user, and global account search is not exposed. Personal extension tokens are shown once, hashed in the database, expire in 1–90 days (30 by default), and are revocable. Every use rechecks membership. Extension tokens cannot manage accounts, create more tokens, or change connector credentials. Copy a token into the extension's API key setting. Workspace tokens authenticate WebSockets with a first authentication frame, not a query string.

Jira is limited to Atlassian Cloud. Teams accepts Microsoft's workflow host suffixes. Generic webhooks require a deployment-managed exact hostname allowlist. Configure network egress restrictions to prevent provider/webhook requests reaching private infrastructure. Stored webhook URLs may contain credentials and should be treated as secret database content.

Natural-language SQL analytics is disabled in SaaS mode because its older semantic views are shared. Workspace dashboard counts, search, extracted notes and drift reports remain available. SaaS reprocessing is disabled to prevent duplicate external tasks; use a reviewed maintenance procedure for failed/partial integrations.

## Processing and operations

SaaS transcript ingestion persists the input and returns `received`; the worker extracts queued transcripts. Clients should poll the transcript status. File-to-text conversion runs during upload and is limited to 100 MiB with a five-minute conversion timeout. Upload storage is temporary; no raw recording is retained. Queue inputs survive API restarts. Interrupted non-live extraction in `chunking`/`extracting` resumes under the workspace worker lock; artifacts and completion are committed before external dispatch. Interrupted live finalization or partial external delivery needs operator inspection; never blindly redispatch completed external side effects.

Workers enumerate workspaces with separate connector configuration and take a PostgreSQL advisory lock for each operation, preventing concurrent worker replicas from running the same workspace operation. Run one worker initially. Configure alerts for `failed`, transcripts stuck in `chunking`/`extracting`, worker tick failures and webhook outbox failures. Connector writes are best effort; extraction completion is not a guarantee that every external integration succeeded.

Default limits are 120 requests/minute/account/workspace, 100 paid-operation attempts/account and workspace/UTC day, 1,000 across the platform/UTC day, 100 MiB requests, two-hour live sessions, 64 KiB audio frames, and one active connection per live transcript. Rejected/failed processing attempts count toward daily quotas. On disconnect, live transcripts persist and remain resumable; only explicit finalization or the session deadline triggers final extraction. Redis leases prevent simultaneous streams; token expiry/membership revocation stops new connections, while already-authenticated streams may continue until closure/deadline.

`/health` is liveness; `/ready` checks PostgreSQL and Redis. API responses use `no-store`; credentials/prompts/transcript text and exception detail are scrubbed from structured production logs. Disable access logs at the ingress too, because legacy development WebSockets can contain a shared key in their URL.

## Deployment artifacts (not deployed)

`compose.production.yml` builds nonroot API/worker containers and a Next.js standalone frontend (Node, nonroot). It does not provision a database, Redis, identity provider, DNS or TLS. Provide those separately. The frontend listens on loopback port 8080 for a TLS ingress; the API is exposed only inside the container network. Configure WebSocket upgrades, request/time limits and HTTPS at the ingress. Database migrations must finish before the API starts. Give migration credentials only to the migration service; API and worker must use `DATABASE_URL` for the runtime role.

When you later choose to host, the intended command is:

```sh
docker compose --env-file .env.production -f compose.production.yml up --build -d
```

Do not run this until secrets, infrastructure and the release checks below are ready. The repository does not publish automatically.

## Release checks and recovery

CI runs backend tests against pgvector, the migration/RLS isolation gate with a nonprivileged role, frontend build, extension tests, runtime dependency audits and both container builds. Database availability is mandatory in CI; missing infrastructure fails instead of skipping these tests. Locally, use an isolated disposable test database and set `TEST_DATABASE_URL`, `SAAS_TEST_ADMIN_URL`, and `REQUIRE_DATABASE_TESTS=true`. The RLS gate creates/drops its own disposable database and role; never point it at a customer cluster.

Before inviting customers, verify the real identity provider login/logout/expiry, valid/invalid extension tokens, two customer workspaces, connector delivery to test channels, upload→worker completion, live reconnect/finalize, and a worker restart. Provider accounts and public HTTPS infrastructure are required for those external checks; unit mocks cannot replace them. Load-test your expected meeting concurrency before raising limits. Set provider-side spending caps and alerts before enabling public signup; request quotas bound attempts, not dollar spend.

Enable encrypted PostgreSQL backups with point-in-time recovery and retain the connector encryption key securely. Run a restore drill into a private isolated database; apply the same runtime-role grants and verify tenant isolation before reconnecting traffic. A release rollback restores a verified database backup plus the matching image revision: workspace migration 0004 intentionally cannot be downgraded destructively. Define your retention period and support process before launch; the API-level export and deletion procedure is in [Customer data requests](#customer-data-requests-export-and-deletion) below, together with what it does not cover. Review privacy/consent and provider contracts for recorded meeting data using your actual business requirements.

## Customer data requests (export and deletion)

Customer data is stored per **workspace**, not per account: tasks, transcripts, chunks, decisions, risks, blockers and outbox deliveries carry a `workspace_id` and no author. Both endpoints are therefore built around workspace ownership. They require a signed-in OIDC session; extension tokens are refused on `/api/account/*`.

### Export — `GET /api/account/data-export`

Returns one JSON bundle (download attachment) for the calling account: the account row, its memberships, its extension-token records, and for every workspace it **owns** the complete contents of all nine workspace tables (`projects`, `transcripts`, `transcript_chunks`, `tasks`, `task_activities`, `decisions`, `risks`, `blockers`, `webhook_deliveries`). Add `?include_embeddings=true` to include embedding vectors, which are derived from chunk text and omitted by default because of their size.

Secrets are listed by name only and never exported: `access_tokens.token_hash`, `workspaces.connector_ciphertext` (the response instead lists which connector fields are configured, without values) and `webhook_deliveries.destination` (a copy of the webhook URL, which may embed a credential). For workspaces shared with other people where the caller is not an owner, the bundle contains only the membership; that content cannot be attributed to one member, and other members' identifiers are not included.

### Deletion — `POST /api/account/data-deletion`

```json
{"account_id": "<the caller's own account id>", "confirmation": "DELETE MY ACCOUNT AND DATA"}
```

Only the authenticated account can erase itself: `account_id` must match the credential and the confirmation text must match exactly, otherwise nothing happens (403 / 422). A read-only `viewer` role in the selected workspace does not block this endpoint, and does not unlock any other write. Everything runs in **one database transaction** (with the account and affected workspace rows locked), including the audit record; any failure rolls all of it back.

| Situation | Result |
|---|---|
| Workspace where the account is the **only member** | Workspace and all its rows are deleted: the nine workspace tables, memberships, every extension token still pointing at it (including tokens of members removed earlier) and the encrypted connector credentials. |
| Workspace **shared with others**, caller is a member, admin, viewer or a co-owner | Only this account's membership and tokens are removed. The workspace and everyone else's data stay. The caller's contributions cannot be told apart from other members', so nothing there is deleted or anonymized. |
| Caller is the **only owner** of a workspace that has other members | Refused with `409` listing the workspaces; nothing is deleted. The owner can remove the other members (`DELETE /api/account/members/{id}`), after which the workspace is private and is deleted. There is no ownership transfer, and this procedure does not promote anyone. |
| Quarantined legacy workspace | Never deleted by this endpoint; only the account's membership in it is removed. |

The account row (which holds the OIDC `sub`) is deleted. The audit record in `data_subject_audit` holds only a timestamp, a keyed digest of the account id, and per-table row counts: no ids, names, subjects or content. The digest is an HMAC under a key derived from `CONNECTOR_ENCRYPTION_KEY`, so an operator can confirm that a given account was erased, and rotating that key breaks that lookup. To keep the table append-only, revoke write access from the runtime role after migrating: `REVOKE UPDATE, DELETE, TRUNCATE ON data_subject_audit FROM aoi_runtime;` (its default grants otherwise allow them). Migration `0005_data_subject_audit` creates the table.

### Operator procedure

1. Do not run SQL for a request that the user can make themselves. Ask them to sign in and use the endpoints (or the API directly). The API is what proves the requester controls the account.
2. For an export, send the bundle over a channel the requester controls; it contains their meeting content.
3. After a deletion, complete the steps this application cannot do (below), and record when each was done.
4. If the requester is blocked by `409`, they must remove the other members first, or you decide with the workspace's members who should own it. Do not delete other members' data to unblock a request.

### What is NOT covered

- **Backups and point-in-time recovery.** Erased rows remain in backups until they expire. If you restore a backup, replay erasures (the audit digests identify which) before reconnecting traffic.
- **Provider-side copies.** Tasks, notes and messages already delivered to Slack, Notion, Linear, Jira, Discord, Teams or Google Calendar, and transcripts or audio sent to the LLM, embedding and speech providers, are governed by those providers' retention. Delete them there, and revoke the connector credentials the workspace used.
- **Webhook receivers.** Payloads already delivered to a generic webhook endpoint are outside our control.
- **The identity provider.** The OIDC account, its email and profile live at your provider. A valid identity that signs in again is provisioned a **new, empty** account and workspace.
- **Shared-workspace content** that mentions a person (names in transcripts, `tasks.owner`, `decisions.decided_by`, `participants`). Matching free text to a person is not attempted.
- **Logs and caches.** Application, ingress and database logs are not searched or purged. Redis holds rate-limit, quota and live-session keys that contain account or workspace UUIDs; they expire on their own (at most 48 hours).
- **Step-up authentication.** Authority comes from the OIDC bearer token plus the confirmation body; the token's age is not checked, so enforce short-lived tokens or a fresh-login requirement at the provider if you need it.

### Keeping it complete

`tests/test_data_subject.py` migrates a throwaway database, runs the endpoints as a nonprivileged role under forced row-level security, queries every table keyed by `account_id` or `workspace_id` after deletion, and checks export completeness, secret exclusion, audit contents and rollback. A schema check fails the build when a table exists that is not classified in `app/services/data_subject.py`, so a new table with an `account_id` or `workspace_id` column must be added to `WORKSPACE_DATA_TABLES` (or `ACCOUNT_TABLES`) and any secret column to `SECRET_COLUMNS` before it can merge.
