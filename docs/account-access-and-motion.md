# Account access and scrolling

Open **Account** at the top-right of any workspace screen. Its panel includes
**Workspace settings** and **Logout** for a signed-in company session. The sidebar
also offers Logout; on mobile, open the navigation drawer to reach it. Logout
returns to `/login`. **Talk to AOI** also accepts “logout” and “open my account”.
Passwords and company credentials still require direct entry.

When a company gateway is configured, `/login` accepts the company code, work email
and password through the existing same-origin identity adapter. OIDC installations
continue through their configured company provider. Invalid credentials and network
failures show an actionable error. Gateway logout waits for an in-flight rotating
refresh, revokes the session through the backend, then clears the in-memory bearer,
workspace selection and API key, plus the saved company marker. A failed logout
shows a retry action instead of claiming the session ended.

Local development without authentication has **Login** in Account and the sidebar.
The login page explicitly explains that company sign-in is not enabled and offers
**Back to local workspace**. It does not accept pretend credentials or create an
account. Enable the existing gateway and restricted database configuration using
[Company identity setup](identity-gateway.md), including `VITE_AUTH_PROVIDER=gateway`
and the matching API configuration, then restart the frontend. Existing OIDC
configuration is also supported. This UI change does not provision companies,
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
