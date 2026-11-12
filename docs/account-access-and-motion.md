# Account access and scrolling

**Logout** is directly visible in the workspace header for signed-in sessions,
including mobile. **Account** also contains **Workspace settings**, **Plans & pricing**
and **Logout**; the sidebar retains Logout. These use the same sign-out action.
**Talk to AOI** also accepts “logout” and “open my account”.
Passwords and company credentials still require direct entry.

When a company gateway is configured, `/login` accepts the company code, work email
and password through the existing same-origin identity adapter. OIDC installations
offer **Continue with SSO** and the configured provider label, using Authorization
Code with PKCE. See [SSO setup and limits](sso.md) for provider registration, public
frontend scope/label values and matching backend JWT verification. One trusted issuer
is supported per deployment; gateway password mode remains separate. No live provider
is configured or activated automatically. Invalid credentials and network
failures show an actionable error. Gateway logout waits for an in-flight rotating
refresh, revokes the session through the backend, then clears the in-memory bearer,
workspace selection and API key, plus the saved company marker. A failed logout
shows a retry action instead of claiming the session ended. OIDC logout uses the
provider's end-session endpoint when present. Otherwise it clears the AOI session
locally and returns to login; the upstream SSO session may remain active. OIDC uses
sessionStorage, validates state/PKCE through the existing library and serializes
single-use callback exchanges. Automatic silent renewal is disabled.

Local development without authentication has **Login** in the header, account panel
and sidebar, with a **Local workspace** label. The login page explicitly explains
that there is no account session, company sign-in is not enabled, and offers
**Back to local workspace**. It does not accept pretend credentials or create an
account. Enable the existing gateway and restricted database configuration using
[Company identity setup](identity-gateway.md), including `VITE_AUTH_PROVIDER=gateway`
and the matching API configuration, then restart the frontend. This UI does not provision companies,
identity credentials or a database.

The sticky header has a slim scroll-position line on longer pages. Newly reached
work sections unfold once; dynamically loaded sections are observed too. Content
is visible before JavaScript runs. Scroll sampling uses one animation frame at a
time, and observers/listeners are removed when leaving a view. Reduced motion hides
the moving line and cancels section movement. Back to top remains available.

## Verification

From `frontend/`, run `npm run test:account-motion`. The test starts isolated Vite
instances for company and local modes, then uses real Chromium with mocked identity
and API responses at desktop and mobile sizes. It checks login, invalid credentials,
logout retry, a new company session without stale workspace headers, sensitive-data
storage, typed/spoken-command handler logout, honest local mode, scroll animation
invocation, progress updates and reduced motion. Recognition is not used by this
suite: the typed command invokes the same execution handler. It does not establish
live gateway/OIDC availability, database isolation or microphone accuracy. The
existing backend identity tests cover the adapter's authentication rules.

Run `npm run test:sso` for isolated desktop/mobile Chromium checks using a synthetic
OIDC provider. They exercise discovery, actual PKCE token exchange, custom scopes,
reload, allowed meeting-tab return, direct header Logout, provider/local logout and
forged-state rejection. No live provider credentials are used. Production frontend
Compose arguments were checked for legacy audience fallback, an explicitly blank
audience and a custom audience; the full production stack was not configured. These
checks do not establish actual Entra, Okta, Auth0 or Google federation, or provider
revocation policy. The [vendor capability comparison](market-research-2026-11.md)
records the rationale for this SSO and meeting-library extension without claiming
customer-demand validation.
