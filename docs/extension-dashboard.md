# AOI extension and dashboard

AOI has two connected surfaces backed by the same API and workspace records:

- **AOI extension (Chrome):** capture the active meeting tab’s audio, view live notes,
  and stop and finalize the meeting.
- **Web dashboard:** review recent meetings, browse paginated meeting history,
  read transcripts and source evidence, manage tasks, review decisions, inspect
  operational drift, and configure workspace connectors.

## Local setup

1. Follow the root README to configure the backend and run `make up`. The
   dashboard is available at `http://localhost:5173` and the API at
   `http://localhost:8000`. Live transcription requires a configured STT provider.
2. In Chrome, open `chrome://extensions`, enable Developer mode, choose
   **Load unpacked**, and select the project’s `extension/` folder. Reload an
   already installed extension after updating its files.
3. Open the extension’s connection settings. Set **Backend URL** to
   `http://localhost:8000` and **Dashboard URL** to `http://localhost:5173`.
   Add the backend API key when required. For account-authenticated deployments,
   create an extension token in the dashboard’s **Workspace & connectors** view.
4. Open a meeting or video tab, click the AOI extension toolbar icon, and choose
   **Start capturing**. **View meeting in dashboard** opens the session at
   `/meetings/{transcript_id}`. It works during capture and after finalization.

For a hosted dashboard, replace Dashboard URL with its origin, for example
`https://ops.example.com`. The dashboard must serve the app at the origin root
and route application paths to its entry HTML; the included Nginx configuration
already supports this. Dashboard links contain no API keys or access tokens.
Sign in separately to the dashboard and select the workspace used by the
extension. A meeting deep link is preserved through dashboard sign-in.

## Dashboard navigation

| Route | Purpose |
| --- | --- |
| `/` | Workspace overview, recent meetings, and attention counts |
| `/meetings` | Meeting history, with 20 meetings per page |
| `/meetings/{id}` | Meeting notes, source quotes, and full transcript |
| `/extension` | Extension setup instructions |
| `/upload` | Import an existing transcript or recording |
| `/tasks`, `/decisions`, `/intelligence` | Follow up across meetings |
| `/weekly-summary` | Leadership briefing |
| `/settings` | Workspace access and connectors |

The overview, meeting history, and meeting details refresh every 10 seconds.
They use existing REST endpoints rather than opening an additional audio
WebSocket. Audio capture and finalization stay in the extension. The dashboard
does not need the extension installed to review records or upload a transcript.
The browser’s Back and Forward buttons and direct route reloads are supported.

Meeting access uses the API’s existing authentication and workspace isolation.
The dashboard link does not select a workspace or transfer extension credentials.
If a meeting is unavailable, check the dashboard’s active workspace and access.
