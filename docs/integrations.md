# Calendar, task sync, and webhooks

These integrations are opt-in and configured on the backend. This is a single-workspace,
single-calendar deployment, not a multi-user OAuth account system. Outlook, CRM connectors
other than Salesforce, and recording imports are not implemented in this change.

## Start or upgrade

1. Configure only the integrations you want in the root `.env` (examples below).
2. Run `make up`. Backend startup applies migration `0002_integrations`; it adds calendar
   metadata, task sync state, and a persistent webhook delivery table without deleting data.
3. Reload the unpacked extension at `chrome://extensions`.
4. Check `GET /api/integrations/status` at `http://localhost:8000/docs`.

New integration routes and task edits require `X-API-Key` when `INGEST_API_KEY` is set.
The existing app is not a multi-tenant authorization boundary; use a trusted deployment.
Provider keys, calendar grants, and webhook signing keys stay on the backend. The extension
only needs the backend URL and its ingestion key.

## Google Calendar

Enable Google Calendar API in your Google Cloud project and create an OAuth client.
Authorize the calendar account with offline access and the read-only scope
`https://www.googleapis.com/auth/calendar.events.readonly`. Exchange the authorization code
for an offline refresh token using your OAuth client (the redirect URI must match its
registration). Save the client ID, client secret, and refresh token server-side:

```ini
GOOGLE_CALENDAR_CLIENT_ID=your-client-id
GOOGLE_CALENDAR_CLIENT_SECRET=your-client-secret
GOOGLE_CALENDAR_REFRESH_TOKEN=your-offline-refresh-token
GOOGLE_CALENDAR_ID=primary
```

The app refreshes an access token when reading the calendar. It does not write events unless
you opt in for the [phone booking agent](voice-calls.md): that needs the broader
`calendar.events` scope plus `GOOGLE_CALENDAR_WRITE_ENABLED=true`.
There is no built-in Google sign-in/consent screen in this version. Obtain the initial grant
using your own OAuth setup. See [Google's offline OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server#offline)
and [event listing reference](https://developers.google.com/calendar/api/v3/reference/events/list).

In the extension, click **Load Google Calendar meetings**, choose a meeting, and then
**Start capturing**. The backend fetches the selected event again and stores its title,
non-declined attendees, date, and event/recurrence identifiers with the transcript. The
meeting title is read-only while a calendar event is selected; choose **Use meeting details
below** to enter it manually. Existing manual capture works without a calendar connection.

Use a project hint for the first meeting in a series. Later instances with the same calendar
and recurrence ID reuse the most recent linked project when the hint is empty. An explicit
hint overrides that association. One-off events do not infer a project from attendee names.
All-day events preserve their calendar date in metadata without inventing a meeting time.

API usage:

- `GET /api/integrations/calendar/events`: from two hours ago through the next seven days;
  accepts timezone-aware `start`/`end` (maximum 31 days) and `page_token`.
- Response: `items` and `next_page_token`. The panel displays the first 100 events; API
  consumers can paginate.
- Pass `calendar_event_id` to `POST /api/live/sessions` or `POST /api/transcripts`.
  Calendar title wins; explicit API participants/date override the calendar defaults.
- Transcript detail responses include `calendar_context`.

## Linear and Jira status sync

Existing extraction creates issues. To synchronize their status in both directions:

```ini
TASK_SYNC_ENABLED=true
INTEGRATION_INTERVAL_SECONDS=60
```

Keep the existing Linear or Jira credentials configured. No historical tickets are imported:
only tasks with an existing `linear_issue_id` or `jira_issue_key` are synchronized. A task
already mirrored to one provider is not duplicated into the other when configuration changes.

The worker checks up to 100 due tasks per pass, including completed tasks so reopening an
issue is reflected locally. It synchronizes before scheduled drift scans. Under normal load,
changes appear within roughly a worker interval; backlogs/network timeouts can take longer.

- A local status edit or extracted status change marks the task `sync_pending`.
- Pending local state wins and is retried until the provider accepts it. Failed pushes retain
  the local value and an actionable `sync_error`; incoming state cannot overwrite it.
- Without a pending local change, the remote provider's status is authoritative.
- Every accepted change is recorded in task activity. Incoming changes update the status
  timestamp and remove completed tasks from subsequent active-task drift checks.
- Task API responses expose `sync_pending`, `sync_checked_at`, and `sync_error`.
- Only **status** synchronizes; owner, priority, title, and due-date edits do not sync remotely.

Default mappings use Linear workflow categories and Jira status categories. Configure custom
states explicitly when needed, using JSON objects whose keys are AOI statuses and whose
values are provider **status IDs**:

```ini
LINEAR_STATUS_MAP={"open":"unstarted-state-uuid","in_progress":"started-state-uuid","blocked":"blocked-state-uuid","done":"completed-state-uuid","cancelled":"canceled-state-uuid"}
JIRA_STATUS_MAP={"open":"10000","in_progress":"10001","blocked":"10002","done":"10003","cancelled":"10004"}
```

Each mapped remote ID must be unique. Linear requires an explicit map when more than one
team state fits a category. Jira selects an available transition whose destination matches;
multiple or unavailable transitions produce an error rather than guessing. Transitions that
require additional fields must be completed in Jira. Custom blocked/cancelled Jira states
need explicit mapping; otherwise generic Jira `done` categories are read as AOI `done`.

References: [Linear GraphQL](https://linear.app/developers/graphql),
[Jira transitions](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-transitions-get).

## Outbound webhooks

Configure one trusted HTTPS destination (for example, your n8n workflow or Zapier catch hook):

```ini
WEBHOOK_URL=https://your-receiver.example/events
WEBHOOK_SECRET=replace-with-a-random-secret-of-at-least-32-characters
WEBHOOK_EVENTS=["meeting.completed","task.created","task.updated"]
```

Generate a secret locally with `openssl rand -hex 32`. The URL and secret are never returned
by the status API. No webhook is queued while the destination is unset. Subscriptions affect
new events; removing a subscription does not discard previously queued events.

Events:

| Type | Trigger and data |
| --- | --- |
| `meeting.completed` | Each successful final extraction, including explicit reprocessing; transcript/project IDs, title, and extracted result (including decisions, risks, blockers, and source quotes). |
| `task.created` | New persisted extraction task, including live extraction; task ID, project, title, owner, priority, due date, and status. |
| `task.updated` | API task edits, extracted status changes, or incoming provider status changes. |

Full transcript text is not sent, but meeting results can contain sensitive source quotes and
participant information. Choose the receiver and subscribed event types accordingly. Tasks
can be emitted during live capture before a meeting is finalized. Duplicate enrichment is
not a separate update event in this version.

Each JSON body contains `id`, `type`, `version`, `occurred_at`, and `data`. The stable event ID
is also sent in `X-AOI-Event-ID`. Deduplicate by that ID: delivery is **at least once**, so a
worker crash after receipt but before marking success can produce a duplicate. Events may
arrive out of order when retried; use `occurred_at` or refetch current state.

Verify signatures against the exact raw request body:

```python
import hashlib
import hmac
import time

def verify(raw_body, timestamp, signature, secret):
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Read timestamp/signature from `X-AOI-Timestamp` and `X-AOI-Signature`. Return 2xx after
accepting the event. Receivers that cannot verify signatures should put verification in a
trusted relay. Redirects are not followed. Network errors, 408, 429, and server failures
retry with backoff, up to eight attempts; other 4xx failures stop immediately.

- `GET /api/integrations/webhooks/deliveries` lists recent outcomes without bodies/secrets.
- `POST /api/integrations/webhooks/deliveries/{id}/retry` requeues a failed delivery using
  its original event ID.
- Unsetting `WEBHOOK_URL` pauses delivery. Changing the destination fails old queued rows
  rather than sending their data to a new receiver; restore the original URL to retry them.
- The current configured signing secret is used for each attempt; coordinate rotation with
  the receiver. Delivery records remain in the database; apply your retention policy.

The existing worker processes the durable outbox. Keep it running alongside the API.

## Salesforce tasks

Each extracted action item can also be created as a Salesforce **Task** record, in addition to
any Linear or Jira issue. Create a Connected App with the `api` and `refresh_token` OAuth scopes,
obtain a refresh token through your own OAuth setup (there is no built-in consent screen, same as
Google Calendar), and save the values server-side. Run `make up` to apply migration
`0005_salesforce_tasks`.

```ini
SALESFORCE_INSTANCE_URL=https://yourorg.my.salesforce.com
SALESFORCE_CLIENT_ID=your-connected-app-consumer-key
SALESFORCE_CLIENT_SECRET=your-consumer-secret
SALESFORCE_REFRESH_TOKEN=your-offline-refresh-token
SALESFORCE_SANDBOX=false
```

The adapter exchanges the refresh token for an access token, caches it in memory, and refreshes
it once if Salesforce answers 401. Set `SALESFORCE_SANDBOX=true` for sandboxes, which authenticate
at `test.salesforce.com`. The instance URL must be an `https` host ending in `.salesforce.com` or
`.force.com`. Check `GET /api/integrations/status` for `salesforce`.

Field mapping: `Subject` is the task title (255 characters maximum), `Description` carries the
task details, source quote, inferred owner, and project, `ActivityDate` is the due date,
`Status` is `Not Started`, and `Priority` is `High` for urgent and high, `Normal` for medium, and
`Low` for low. The record id and Lightning URL are stored on the task and returned as
`salesforce_task_url`, so a task is never created twice.

Limitations:

- Creation is at least once. Network errors, HTTP 429, and 5xx are retried up to three times with
  exponential backoff (like the other adapters), so a response lost after Salesforce saved the
  record can leave a duplicate Task. 4xx errors are not retried and only the HTTP status and
  Salesforce error code are logged, never message text or credentials.
- Creation happens once, right after extraction, and is best effort like the other connectors.
  A failed create is not retried later and does not use the webhook outbox.
- It is one-way. Status changes in Salesforce are not synced back, and records are not linked to
  Accounts, Contacts, or Opportunities. The Task is owned by the integration user.

## Console authentication

When `INGEST_API_KEY` is configured, enter it in **Backend API key (if set)** in the web
console sidebar before editing tasks or uploading transcripts. It is kept only in memory
and cleared on reload. The console and extension accept the backend ingestion key, never
Google OAuth credentials, provider API keys, or the webhook signing secret.
