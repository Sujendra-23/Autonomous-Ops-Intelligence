# Meetings and huddles inside AOI

The Meetings tab has three views: **Upcoming**, **Huddles**, and **Meeting notes**.
Start a meeting now, schedule one for later, or create a reusable audio huddle.
Join links open an AOI room in the same company workspace. They do not grant
anonymous guest access; invitees need access to that workspace.

The selected view persists through refresh and browser history with
`/meetings?tab=upcoming`, `/meetings?tab=huddles` or `/meetings?tab=captured`.
Meeting notes keeps the existing `captured` route value. Home's **All notes** opens
Meeting notes; a transcript's back action returns there. **All meetings** from a
huddle room returns to Huddles, while other rooms return to Upcoming. **Talk to AOI**
handles “Go to meeting notes”, “Go to transcripts” and “Go to huddles” locally.

Before joining, enter a display name and choose the microphone/camera state.
Devices are opened only after Join. Meetings support camera video; huddles exclude
camera publication in their server-issued permissions. Both support microphone
audio, screen sharing, live chat, a participant list, device selection, and Leave.
Screen sharing uses the browser's chooser; browser/platform support varies.
Leaving or navigating away disconnects the room and stops local media tracks.
Chat is live and is not stored as a workspace transcript. Calls are not recorded
automatically; the existing AOI Desktop / AOI extension capture workflows remain
separate. Production calls require HTTPS and a reachable media server.

## Run locally

Requires Docker Desktop and the normal AOI backend/database setup:

```sh
docker compose -f docker-compose.yml -f compose.calls.yml up -d livekit backend
```

Apply `alembic upgrade head` if running the API outside the normal compose startup.
Migration `0012_meeting_rooms` adds workspace-owned room records, forced row-level
policies, request de-duplication and calendar invitation status. The local overlay
uses public development-only LiveKit credentials and loopback ports 7880/7881/7882;
it supports browsers on the same machine. Keep the existing Vite frontend running
at `http://localhost:5173`. This overlay is not a public deployment configuration.

A native API can use `LIVEKIT_URL=ws://localhost:7880`, `LIVEKIT_API_KEY=devkey`,
`LIVEKIT_API_SECRET=secret` and `APP_PUBLIC_URL=http://localhost:5173` against that
local development media server. If the API is in Docker, set its internal
`LIVEKIT_API_URL=http://livekit:7880` as the overlay does.

For LiveKit Cloud or a production self-hosted server, set `LIVEKIT_URL` to its
browser-reachable `wss://` origin, set backend-only `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET`, and set `APP_PUBLIC_URL` to AOI's HTTPS origin. Optional
`LIVEKIT_API_URL` sets the server API origin. Deploy proper TLS, ICE/UDP and TURN
connectivity following LiveKit's [deployment guide](https://docs.livekit.io/transport/self-hosting/deployment/)
and [port reference](https://docs.livekit.io/transport/self-hosting/ports-firewall/).
A local two-browser test does not establish cross-network, firewall or TURN behavior.

## Scheduling and invitations

The short form asks for title, date, start time and duration. More options contains
time zone, notes and a calendar reminder. The server validates IANA time zones,
future starts, elapsed duration, attendee limits and daylight-saving gaps/repeated
times. Repeated or nonexistent local times ask the user to choose a different time.
An AOI meeting is saved independently of calendar availability.

If Google Calendar is connected, **Send Google Calendar invitations** optionally
reveals teammate email entry. The submit action clearly changes to Schedule &
invite. Calendar invitations contain the AOI room link, not an external Meet link.
Older read-only grants need reconnecting with `calendar.events` permission alongside
the existing calendar-reading permission. Google supports event creation and guest
notifications through its [event insertion API](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).
No real invitation is sent by automated tests.

Client attempts reuse a request UUID; the database scopes its unique constraint to
the workspace. Retrying the same form does not create another AOI room, and changed
intent is rejected rather than silently reusing a room. Calendar event IDs derive
from the workspace/request; a provider conflict retrieves and compares the stored
payload hash before returning an existing event. Network/provider failure preserves
the AOI room and reports unconfirmed invitations. An explicit Retry calendar
invitations action reuses the saved intent. It does not create another room.

Talk to AOI can navigate to Meetings, prepare or save a schedule, and use visible
call controls. Supply an explicit date/time and exact teammate email addresses when
inviting. The planner receives opaque email aliases; the server restores only the
addresses the user supplied into the invitation field. It rejects invented invitees.
Browser permission prompts and the screen chooser still need the user's interaction.

## Access and media credentials

Room reads and join-token issuance require the current workspace. The database
forces row-level policies, and API lookups explicitly include that workspace.
Read-only workspace members may join a room and participate in a call; this narrow
join exception does not permit meeting creation or other workspace mutations.
Server-side LiveKit calls provision a maximum of 50 participants per media session.
Participant JWTs expire after two minutes for initial connection and are scoped to
one opaque room and participant identity, without room administration/recording
grants. The API secret never enters the frontend. See LiveKit's
[token lifecycle](https://docs.livekit.io/frontends/reference/tokens-grants/) for
connection/reconnect behavior; token expiry is not a call-duration limit.

Presence comes from the media server. An unavailable presence service remains
unknown rather than inventing an empty room. Room records join account data exports
and erasure handling; deleting an account clears its creator reference on shared
rooms. Removing data from AOI does not delete copies held by calendar providers.

## Verification

For the UI navigation checks, start Vite in `frontend/` with `npm run dev`, then run
`npm run test:simple-ui` in another terminal. It uses `http://127.0.0.1:5173` by default;
set `AOI_TEST_URL` for another local Vite origin. The release-checks calls job runs
this suite against its existing Vite server. Mocked API and speech-recognition
responses cover desktop/mobile navigation, Reports, Account pricing, notes-first Home
order, tab refresh/history, transcript return, huddle prejoin/back and spoken tab
navigation. Checks assert no page errors or horizontal overflow, make no real
workspace writes and open no media devices. This UI suite passed alongside the build,
`test:browser` and `test:account-motion`; the live call/backend checks below remain
separate and were not rerun for this UI-only change.

`frontend/tests/calls-live.test.mjs` runs against an isolated API/PostgreSQL/LiveKit
stack. Chromium uses fake microphone/camera sources and a generated canvas for
screen sharing. Two participants exchange audio, shared-screen video and chat;
the test checks huddle camera restrictions, meeting camera publication, mobile
control placement, provider participant counts, local peer cleanup on Leave, and
persisted scheduling. No real camera, microphone, screen or private call is captured.
This is a browser transport test, not a real-device or multi-network load benchmark.
Run `npm run test:calls` from frontend with `AOI_CALL_TEST_URL` pointing to the
isolated Vite server. Never point this write-capable suite at a shared workspace.

From `backend/`, run the normal suite with `TEST_DATABASE_URL`,
`SAAS_TEST_ADMIN_URL` and `ANALYTICS_TEST_ADMIN_URL` pointing to isolated databases.
Database fixtures require an explicit test URL and never fall back to the app DB.
New tests cover wall-clock/DST validation, scoped token grants, missing configuration,
workspace lookup, idempotent retries and calendar failure preservation. Existing
forced-policy, export and erasure checks include meeting room records.
