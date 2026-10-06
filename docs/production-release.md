# Public SaaS release preparation

No hosting or customer accounts were created by this change. The application now supports verified accounts, separate workspaces, roles, workspace-bound extension tokens, encrypted connector settings and database-enforced isolation. Hosting remains an explicit later step.

## Identity and configuration

Use a standards-compliant OIDC provider with a public browser client using **Authorization Code + PKCE**, signed RS256/ES256 access tokens, and an API audience. Configure the exact issuer, audience and HTTPS JWKS endpoint. Register `https://YOUR-APP-DOMAIN/auth/callback` and the app origin as allowed logout/redirect URLs. Enable verified signup and account recovery at the provider. Passwords are never stored in this application. Access-token expiry returns the browser to sign-in; tokens are not silently renewed.

Copy `.env.production.example` to `.env.production`, set mode 0600, and replace placeholders. Browser client IDs are public; never put client secrets, API keys or connector credentials in Vite variables. The API rejects production/staging with development authentication. Set platform LLM/STT keys and explicit HTTPS CORS origins. A Redis outage blocks authenticated requests rather than allowing unlimited paid work.

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

`compose.production.yml` builds nonroot API/worker containers and a static Nginx frontend. It does not provision a database, Redis, identity provider, DNS or TLS. Provide those separately. The frontend listens on loopback port 8080 for a TLS ingress; the API is exposed only inside the container network. Configure WebSocket upgrades, request/time limits and HTTPS at the ingress. Database migrations must finish before the API starts. Give migration credentials only to the migration service; API and worker must use `DATABASE_URL` for the runtime role.

When you later choose to host, the intended command is:

```sh
docker compose --env-file .env.production -f compose.production.yml up --build -d
```

Do not run this until secrets, infrastructure and the release checks below are ready. The repository does not publish automatically.

## Release checks and recovery

CI runs backend tests against pgvector, the migration/RLS isolation gate with a nonprivileged role, frontend build, extension tests, runtime dependency audits and both container builds. Database availability is mandatory in CI; missing infrastructure fails instead of skipping these tests. Locally, use an isolated disposable test database and set `TEST_DATABASE_URL`, `SAAS_TEST_ADMIN_URL`, and `REQUIRE_DATABASE_TESTS=true`. The RLS gate creates/drops its own disposable database and role; never point it at a customer cluster.

Before inviting customers, verify the real identity provider login/logout/expiry, valid/invalid extension tokens, two customer workspaces, connector delivery to test channels, upload→worker completion, live reconnect/finalize, and a worker restart. Provider accounts and public HTTPS infrastructure are required for those external checks; unit mocks cannot replace them. Load-test your expected meeting concurrency before raising limits. Set provider-side spending caps and alerts before enabling public signup; request quotas bound attempts, not dollar spend.

Enable encrypted PostgreSQL backups with point-in-time recovery and retain the connector encryption key securely. Run a restore drill into a private isolated database; apply the same runtime-role grants and verify tenant isolation before reconnecting traffic. A release rollback restores a verified database backup plus the matching image revision: workspace migration 0004 intentionally cannot be downgraded destructively. Document customer-data retention/deletion and support procedures before launch; database deletion must include tenant artifacts, chunks, outbox deliveries, tokens and memberships and follow your backup retention policy. Review privacy/consent and provider contracts for recorded meeting data using your actual business requirements.
