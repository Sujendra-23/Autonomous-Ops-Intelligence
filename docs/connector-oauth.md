# Connector sign-in

The dashboard and browser extension connect Google Calendar, Slack, Notion,
Linear, Jira and Discord through their provider's OAuth consent page. Microsoft
Teams uses Microsoft identity OAuth and Microsoft Graph to publish channel
messages as the signed-in user. Connections are saved to the active workspace;
users do not need to copy tokens or restart the server after authorizing.

In the dashboard, open **Workspace & connectors → Connect your tools**. In the
extension, open **Connectors**, expand a tool, and click **Connect**. Complete
sign-in in the new tab and approve access. The original view polls for completion
and updates automatically. Slack and Discord provide a channel picker during
consent. A unique destination is selected automatically. When Notion, Linear,
Jira or Teams returns multiple destinations, choose the destination in Connectors.
Jira and Teams can require two choices (site then project, or team then channel).
No data is published to an arbitrary first destination.

Connecting enables the existing meeting publishing behavior; Linear/Jira also
enable status reconciliation. The existing dispatcher prefers Linear when both
Linear and Jira are connected. Generic signed webhooks retain manual URL/secret
setup because there is no provider OAuth login for a custom receiver.

## One-time deployment setup

Apply the existing workspace migration (`alembic upgrade head`) and run Postgres
and Redis. Set `CONNECTOR_ENCRYPTION_KEY` to a generated Fernet key, preserving
it across restarts. This key is also required for development OAuth connections.
Set `CONNECTOR_OAUTH_REDIRECT_BASE` to the public HTTPS app origin. Development
can use `http://localhost:8000`. The production nginx configuration proxies the
callback path to the backend. If another proxy is used, proxy `/oauth/connectors/`
and avoid logging callback query strings (authorization codes/state).

Register an OAuth app with each provider you want to offer. Configure its client
ID/secret in the backend environment and register the exact callback URL below.
Restart the backend and workers when changing app registration settings. Empty
client settings produce an actionable setup error; they do not simulate a
successful connection. Client secrets and access/refresh tokens are never returned
by status or attempt APIs. Frontend `VITE_AUTH_*` settings are for signing into AOI,
not for signing into a connector.

| Provider | Client environment fields | Callback suffix | Required access/configuration |
| --- | --- | --- | --- |
| Google Calendar | `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET` | `/oauth/connectors/google_calendar/callback` | Enable Calendar API; `calendar.readonly`, offline access; configure consent/testing users |
| Slack | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | `/oauth/connectors/slack/callback` | Bot scopes `chat:write`, `incoming-webhook`; enable incoming webhooks; choose a channel |
| Notion | `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET` | `/oauth/connectors/notion/callback` | Public connection with read, insert, update content capabilities; share a parent page during consent |
| Linear | `LINEAR_CLIENT_ID`, `LINEAR_CLIENT_SECRET` | `/oauth/connectors/linear/callback` | `read`, `write`; PKCE; grant team access |
| Jira | `JIRA_CLIENT_ID`, `JIRA_CLIENT_SECRET` | `/oauth/connectors/jira/callback` | 3LO app; `read:jira-work`, `write:jira-work`, `offline_access`; permission to create issues in the chosen project |
| Discord | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | `/oauth/connectors/discord/callback` | `webhook.incoming`; permission to manage webhooks in the selected channel |
| Teams | `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET`, `TEAMS_TENANT_ID` | `/oauth/connectors/teams/callback` | Web app registration with delegated `User.Read`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`, `ChannelMessage.Send`, `offline_access`; a licensed Teams account; tenant consent may be required |

Teams defaults to the `organizations` tenant. Set a tenant ID for a single-tenant
registration. The picker offers standard channels in joined teams. Existing
Teams Workflow webhook configuration continues to work as an alternative.
Manual deployment credentials remain supported in development. SaaS workspaces
never inherit another workspace's or the deployment's connector grants.

## When Connect does not open sign-in

The button immediately shows progress and any error beside the connector. A
**Setup required** badge means the backend is reachable but the provider app
has not been configured. Add that provider’s client ID and secret from the table
above, set connector encryption, register the callback, and restart the backend.

If the extension says **Cannot reach the AOI backend**, check **Backend
connection → Backend URL**. For local development, start Docker Desktop, then run
`docker compose up -d --build` from the repository root. The default API URL is
`http://localhost:8000`. An old backend returning 404 must be updated and restarted.
Reload the unpacked extension at `chrome://extensions` after updating its files,
then close and reopen the side panel. Updating source files alone does not refresh
an already-open extension panel.

## Lifecycle

Initiation and configuration changes require workspace administrator access.
The extension may use an administrator's AOI workspace token. Provider callbacks
are public, but require an encrypted, random, ten-minute, one-use state tied to the
initiating account, workspace, provider and PKCE verifier where supported.
Callbacks recheck membership and whether the attempt has been superseded or
cancelled. Concurrent attempts do not move a grant to a different workspace.
Redis failures stop sign-in rather than bypass state validation.

Credentials, destination choices and refresh metadata are encrypted in the
existing workspace connector column. Expiring grants refresh on use under a
workspace row lock, including in worker jobs. Google Calendar retains the
existing offline-grant refresh behavior. A denied or failed grant preserves the
previous connection. Selecting a destination enables publishing only after the
selection succeeds.

**Disconnect** removes AOI's local credential and destination and invalidates
outstanding sign-in attempts. To revoke the provider-side app authorization as
well, remove AOI from that provider's connected apps settings. This distinction
also matters for Discord/Slack webhook installations retained by the provider.

Provider permissions and tenant policies may require consent or later
reconnection. OAuth does not import all historical provider data: it enables
AOI's existing calendar access, note publishing, task mirroring and notifications.

Protocol references: [Google](https://developers.google.com/identity/protocols/oauth2/web-server),
[Slack](https://docs.slack.dev/authentication/installing-with-oauth),
[Notion](https://developers.notion.com/guides/get-started/authorization),
[Linear](https://linear.app/developers/oauth-2-0-authentication),
[Jira](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/),
[Discord](https://docs.discord.com/developers/topics/oauth2),
[Teams channel messages](https://learn.microsoft.com/en-us/graph/api/channel-post-messages).

## Pricing preview

The dashboard’s **Plans & pricing** view and public `/pricing` route show Free,
Pro, and Team. Pro is **$50 per company per month for 10–20 members**. Team is
for larger companies, with **custom monthly pricing based on company size**.
Sales contact details will be added when the company email is available. The
extension includes the same pricing preview. Final entitlements and usage limits
remain to be confirmed. No checkout, billing, or plan enforcement is enabled.
