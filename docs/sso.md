# Company SSO and logout

**Logout** is directly in the signed-in workspace header, in Account and at the
bottom of navigation. All three use the same action. Local development has no account
session: it shows **Login** and a Local workspace label instead. `/login` explains this.

AOI uses OpenID Connect Authorization Code with PKCE through `oidc-client-ts`.
It supports one configured, trusted authorization server per deployment. An identity
broker can federate several upstream providers. AOI does not implement SAML or SCIM.
The existing Identity Access Gateway password flow remains a separate deployment mode;
its SSO federation must be configured at that gateway, not mixed with unrelated OIDC tokens.

## Connect a provider

1. Register AOI as a public single-page client. Enable authorization code + PKCE and
   CORS for the AOI origin. Never put a client secret in the browser.
2. Register the exact callback `https://YOUR-APP/auth/callback` and post-logout URL
   `https://YOUR-APP` (the origin, without a trailing slash). Use the corresponding
   localhost origin for a development registration.
3. Configure an API/resource that issues signed JWT access tokens for AOI. Set the
   backend `AUTH_MODE=oidc`, `AUTH_PROVIDER=oidc`, `AUTH_ISSUER`, `AUTH_AUDIENCE` and
   `AUTH_JWKS_URL` to that API's issuer, audience and HTTPS signing-key endpoint.
   Keep the runtime PostgreSQL role, encryption key and workspace-policy setup from
   the production guide. An ID token or a token for Microsoft Graph is not an AOI API token.
4. Set public frontend values in `frontend/.env.local` for Vite, or build arguments
   for the production image, then restart/rebuild:

```dotenv
VITE_AUTH_PROVIDER=oidc
VITE_AUTH_AUTHORITY=https://YOUR-OIDC-AUTHORITY
VITE_AUTH_CLIENT_ID=YOUR-PUBLIC-SPA-CLIENT-ID
VITE_AUTH_PROVIDER_NAME=Company SSO
VITE_AUTH_SCOPE=openid profile email YOUR-AOI-API-SCOPE
# Optional provider-specific authorization audience, e.g. Auth0:
VITE_AUTH_AUDIENCE=
```

Microsoft Entra ID: use a tenant-specific v2.0 authority, register a SPA redirect,
expose an AOI API delegated scope and include it in `VITE_AUTH_SCOPE`. Set the backend
issuer/audience to the resulting AOI access-token claims. Leave `VITE_AUTH_AUDIENCE`
explicitly blank: v2 uses scopes to select the API. Production Compose preserves
the previous AUTH_AUDIENCE fallback when VITE_AUTH_AUDIENCE is entirely unset; an
explicit empty value disables that frontend parameter. See [Microsoft's authorization-code guidance](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow).

Okta/Auth0: use an authorization server configured to issue access tokens for your
AOI API, with matching issuer/JWKS/audience. Auth0's API identifier can be supplied as
`VITE_AUTH_AUDIENCE`. Google Workspace can be federated through an OIDC broker that
issues AOI-audience JWT access tokens; direct Google API access tokens do not satisfy
AOI's JWT API verifier. See the [OIDC client documentation](https://authts.github.io/oidc-client-ts/).

SSO authentication does not automatically merge workspaces by email domain. A new OIDC
account receives its own workspace; an owner can add it to a company workspace from
Settings using its account ID. Gateway mode retains its company-to-workspace mapping.

## Session behavior and checks

The library stores the OIDC session in sessionStorage; gateway bearer tokens remain
in memory. Callback state and PKCE are validated by the library. Callback exchanges
are serialized so React StrictMode cannot reuse a single-use code. Return routes use
an explicit allowlist. Invalid callbacks cannot enter the workspace.

Logout clears the AOI session and redirects to the provider's end-session endpoint
when available. Providers without that endpoint use local AOI logout; their upstream
SSO session may remain active. Tokens expire according to provider policy; silent
renewal is disabled. The backend verifies token signatures, issuer, audience and expiry
and enforces workspace membership. No new identity provider has been provisioned by
these code changes; live activation needs your provider registration and deployment values.

`cd frontend && npm run test:sso` starts an isolated Vite server and uses a synthetic
OIDC provider in Chromium. Desktop/mobile checks cover discovery, PKCE token exchange,
custom scopes, return-tab restoration, reload, visible header Logout, provider/local
logout and forged-state rejection. No external account or workspace writes are used.
Backend verifier checks are in `backend/tests/test_saas.py`. These tests do not establish
actual Entra/Okta/Google federation or provider-side revocation behavior.
