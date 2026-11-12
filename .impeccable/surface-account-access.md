# Account access direction

Mode: Operate. Scope: make login and logout visible within the established workspace.
This extends the incumbent cream, charcoal and lavender world, with Figtree controls
and regular/italic EB Garamond introductions. DESIGN.md and .impeccable/design.json
remain the visual authority; this surface introduces no new system tokens.

## Entry and composition

Keep Logout directly visible in the sticky workspace header for authenticated
sessions. In local mode, show Login beside the Local workspace control and explain
that there is no account session. The Account control's lavender initial
and outlined pill open a paper panel containing the workspace name, session context,
Workspace settings, Plans & pricing and Logout. Local development substitutes Login
and states that there is no account session. The sidebar retains Settings and the
session entry at the bottom; workspace identity is a static label. Pricing remains
available at its existing route through Account. The panel stays
within the viewport; below (620px), the trigger retains its accessible Account name
while showing the avatar alone. Escape restores focus to the trigger; outside clicks
and focus leaving the panel dismiss it.

The `/login` route pairs a cream editorial introduction with a paper form, stacking
below (800px). Gateway mode uses labeled company code, work email and password fields,
followed by a lavender submit action. Busy and actionable error states remain explicit.
OIDC offers Continue with SSO, names the configured provider and leaves password
entry with that provider. Local mode explains that account login is
not enabled and offers Back to local workspace. Preserve honest setup requirements;
the interface does not create credentials or enable a provider automatically.

## Session behavior

Successful gateway logout revokes the server session before clearing in-memory
credentials, workspace selection and API key, removing the company marker, and
returning to login. A failed request leaves a visible retry path. Account, sidebar,
the direct header action and the explicit Talk to AOI logout command use the same
sign-out action. Passwords require direct entry. Gateway credentials stay out of web
storage; the existing
OIDC session-storage policy is unchanged. Authorization Code with PKCE uses the
existing OIDC library, configurable scopes and one trusted issuer per deployment.
Serialize callback exchanges so React StrictMode cannot reuse a single-use code.
Provider logout uses the discovered end-session endpoint, with local AOI logout when
it is absent; the upstream session may remain active. Gateway password mode remains
separate. Setup is in docs/sso.md; no live provider was configured or activated.

Meeting-view return links allow only the known `upcoming`, `huddles` and `captured`
query values during OIDC callback restoration; other return paths retain the strict
existing route allowlist.

## Evidence and limits

The fresh final review from `account_motion_finish_reviewer` returned SHIP with no
material fixes. Final desktop/mobile evidence is in `.impeccable/review/login-desktop.png`,
`login-mobile.png`, `account-desktop.png` and `account-mobile.png`.
Isolated Chromium checks at (1440px) and (390px), with mocked identity and API
responses, covered invalid credentials, logout failure/retry, new-company credential
cleanup, gateway storage, command-handler logout and honest local mode. The command
check invoked the shared handler through typed input. It does not verify microphone
recognition, a real gateway/OIDC deployment or database isolation. Setup and the
repeatable test command are documented in docs/account-access-and-motion.md.

The later UI simplification retained these session and motion behaviors;
`test:account-motion` passed again and `test:simple-ui` covers Account pricing access.
Its scoped correction review and Home/navigation evidence are in surface-dashboard.md;
it did not repeat live identity verification.

The SSO/library extension passed a scoped correction review after preserving separate
recap/follow-up drafts; no observed regressions remained within that scope. Latest
account captures are `.impeccable/review/sso-login-{desktop,mobile}.png` and
`logout-header-{desktop,mobile}.png`. At (1440px) and (390px), `test:sso` used an
isolated Vite server and synthetic OIDC provider to exercise real PKCE token exchange,
custom scopes, reload, allowed meeting-tab restoration, direct header Logout,
provider/local logout and forged-state rejection. Existing `test:browser`,
`test:account-motion` and `test:simple-ui` also passed. This is not a whole-product
review or live identity-provider acceptance. The incumbent visual system was compared,
not regenerated; no new global tokens were introduced.
