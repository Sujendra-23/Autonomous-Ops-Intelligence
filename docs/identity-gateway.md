# Company identity and PostgreSQL isolation

AOI integrates with [Multi-Tenant Identity & Access Gateway](https://github.com/Sujendra-23/Multi-Tenant-Identity-Access-Gateway), reviewed at commit `17710570ef4fce276b07df1214a3b59795a035eb`.

**Primary database: PostgreSQL with pgvector.** AOI keeps meeting records, company configuration, tasks, decisions and embeddings in PostgreSQL. Redis holds session/rate-limit state. The gateway owns its own identity database; AOI does not read or copy its password tables. The optional local compose services use a separate PostgreSQL database for identity to avoid migration and role collisions. Both logical databases can live on a shared PostgreSQL cluster when provisioned with separate roles and databases.

## What is connected

- A company gateway token is independently verified against Ed25519 JWKS, issuer, audience and expiry. The signed `tid`/`tsl` identify the company; unsigned `X-Gateway-*` headers never authorize a request.
- Every authenticated HTTP request and WebSocket connection checks `/v1/auth/me` with the gateway, preserving session revocation and policy-version checks. Gateway/network failure refuses access.
- Each `(identity issuer, gateway tenant UUID)` maps to exactly one AOI workspace. Different users of the same company join that workspace; first login does not create a private workspace for every employee. A requested workspace ID cannot override the company in the signed token.
- AOI's existing forced PostgreSQL row-level policies, scoped queries, composite foreign keys, encrypted connectors and company-scoped workers remain active. `AUTH_MODE=oidc` is deliberately retained for this enforcement; `AUTH_PROVIDER=gateway` selects the identity adapter.
- The gateway's bundled `owner` maps to AOI `owner`; its bundled read-only `member` maps to AOI `viewer`. Unknown/custom roles are refused until an explicit authorization adapter is implemented. This integration does not reinterpret custom gateway permission/deny rules as AOI privileges.
- Companies and membership roles are managed in the gateway. Company owners can rename their AOI workspace and configure its connectors independently. Local workspace/membership mutation endpoints cannot bypass gateway ownership.

## Start the gateway locally

If your gateway is already running, use its origin in the AOI settings below. Otherwise:

```bash
cp .env.identity.example .env.identity
chmod 600 .env.identity
```

Replace each placeholder with a different URL-safe random secret (`openssl rand -hex 32`). Then run:

```bash
docker compose --env-file .env --env-file .env.identity \
  -f docker-compose.yml -f compose.identity.yml \
  up -d --build identity-gateway
```

The gateway API is `http://localhost:8180` and readiness is `/readyz`. Its PostgreSQL and Redis ports are private to the compose network. The gateway uses a restricted `identity_app` database role; its bootstrap role is not an application login. Database initialization runs only for a fresh identity volume.

Provision each company and its owner using the gateway's authenticated operator endpoint `POST /v1/admin/tenants`; see the upstream README. Do not put the bootstrap key in frontend configuration. Use the gateway's authenticated user/role endpoints to manage company members. AOI does not provision companies from arbitrary sign-in form input.

## Enable company sign-in in AOI

1. Apply `alembic upgrade head` using the migration role. Migration `0007_identity_gateway` adds nullable identity bindings and a unique issuer/company constraint; existing records and the quarantined legacy workspace are preserved.
2. Configure the API and worker:

```ini
AUTH_MODE=oidc
AUTH_PROVIDER=gateway
AUTH_ISSUER=https://gateway.local
AUTH_AUDIENCE=identity-gateway
AUTH_JWKS_URL=http://identity-gateway:8080/.well-known/jwks.json
IDENTITY_GATEWAY_URL=http://identity-gateway:8080
# Generate and retain a Fernet key for workspace connector encryption.
CONNECTOR_ENCRYPTION_KEY=<your-Fernet-key>
# Use the restricted AOI role documented in production-release.md, not a superuser.
DATABASE_URL=postgresql+asyncpg://aoi_runtime:<password>@postgres:5432/aoi
```

For an API running natively rather than in compose, use `http://localhost:8180` for gateway and JWKS URLs. Match issuer/audience to the actual gateway configuration. HTTPS is required for issuer, gateway and JWKS outside development. The application refuses SaaS startup with a superuser or BYPASSRLS database role. The existing development compose setup uses a bootstrap database role, so provision the restricted runtime role before switching authentication modes; keep migration credentials separate.

For compose, put `AOI_RUNTIME_DATABASE_URL` and the retained `CONNECTOR_ENCRYPTION_KEY` in `.env.identity` after provisioning that runtime role and applying migrations. Enable the complete company flow with:

```bash
docker compose --env-file .env --env-file .env.identity \
  -f docker-compose.yml -f compose.identity.yml -f compose.company.yml \
  up -d --build
```

`compose.company.yml` overrides the base compose database connection so the API and worker use the restricted role, and starts the runtime without attempting schema migrations. Its frontend container enables company sign-in and proxies requests to the backend service. If you already have a native Vite server on port 5173, stop that server before starting the compose frontend, or start only `backend worker identity-gateway` and use the native frontend setting below.

3. Put this in `frontend/.env.local` and restart Vite:

```ini
VITE_AUTH_PROVIDER=gateway
```

For production, the frontend Docker build accepts the same `VITE_AUTH_PROVIDER` argument. The production compose file derives it from `AUTH_PROVIDER`. The frontend and backend must use the same authentication provider. When using OIDC instead, retain your OIDC authority/client settings; the existing OIDC flow remains supported.

4. Open `http://localhost:5173` and sign in with the company's code, email and password. The sidebar shows the verified company's workspace name. Meeting deep links survive login. In **Workspace & connectors**, company owners can change the workspace name and connect their tools.

The dev server proxies `/api` and `/auth/gateway` to the local backend; production Nginx proxies the same routes. Same-origin login avoids cross-site refresh-cookie issues. An explicit `VITE_API_URL` still requires appropriate backend CORS configuration.

## Sessions and the extension

Refresh tokens are stored only in company-scoped HttpOnly cookies under `/auth/gateway`. Access tokens remain in browser memory. Only the company code is stored in sessionStorage. Cookie refresh is serialized across same-origin tabs via Web Locks and within a page to respect single-use rotation. Reloading restores the selected company's session. Signing out revokes its gateway session and removes its refresh cookie.

In gateway mode, use **Copy extension session token** in Workspace & connectors, then paste it into the extension's **API key** field. This is a short-lived signed gateway access token; copy a fresh token before starting a new capture after it expires. Legacy long-lived `aoi_` tokens are deliberately refused in gateway mode because they would bypass the gateway's live-session checks. Existing OIDC deployments retain their revocable workspace extension tokens.

Already-open audio WebSockets are authenticated at connection time; session changes do not retroactively close a stream. Captures remain bounded by the existing session deadline. Custom permission routing, gateway risk scoring for every AOI operation, organization self-service signup, payment enforcement, and a sales email are not supplied by this adapter.

## Validation

`tests/test_identity_gateway.py` tests signatures/claim rejection, current gateway checks, revocation, read-only roles, cookie boundaries and CSRF rejection. With `SAAS_TEST_ADMIN_URL` set, it creates and drops an isolated PostgreSQL database and restricted role to test company mapping, shared company membership, rejected workspace switching, raw SQL isolation and missing-context denial. It never resets the application database.
