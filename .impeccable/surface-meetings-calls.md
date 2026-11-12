# Meetings and calls direction

Mode: Operate. Scope: schedule and join meetings inside AOI, or drop into a reusable
audio huddle with teammates. This extends the incumbent workspace. DESIGN.md and
.impeccable/design.json remain the visual authority and are preserved unchanged.

## Entry and composition

Use the existing cream shell, editorial EB Garamond page heading and Figtree controls.
The Meetings heading pairs a quiet Schedule action with the primary Start meeting
action. Underlined Upcoming, Huddles and Meeting notes controls keep scheduled rooms,
drop-in conversations and captured transcripts easy to find. Huddles changes the
primary action to Start huddle. Divided room rows show the title, time or drop-in
guidance, known participant presence, Join and Copy link. Empty views explain the next
action; Meeting notes retains the history and transcript-upload path.

The view persists at `/meetings?tab=upcoming|huddles|captured`; `captured` is the
unchanged route value behind Meeting notes. Home's All notes and transcript-detail
back action open Meeting notes. A huddle room's All meetings returns to Huddles;
other rooms return to Upcoming. Local Talk to AOI navigation understands meeting
notes, transcripts and huddles. These entrypoint refinements preserve existing paths.

## Meeting library and follow-up

Meeting notes adds labeled title/transcript search, status and time filters above the
divided list. Apply with Search notes; Clear filters restores the library. Status
offers Completed, Live and Failed; time offers all time or the last 7, 30 or 90 days.
Show filtered totals and Previous/Next pages of 20 records. Empty filtered results
suggest changing the query or period; errors retain an explicit Refresh notes path.
Queries are bounded and wildcard characters are literal; records remain workspace-scoped.

Transcript detail keeps Recap & follow-up collapsed until requested. Within the fine
divider treatment, a format selector and labeled Figtree textarea prepare a plain-text
recap or follow-up from saved tasks, decisions, risks and blockers. Copy and Download
use the current edited format; Download transcript preserves the full saved transcript.
Keep independent edits when switching formats until detail unmounts; leaving the view
discards them. Clipboard denial explains manual copying. State that nothing is sent
automatically and ask users to review details and recipients. This is deterministic
formatting of saved outcomes, not new AI abstractive summarization or public sharing.

On narrow screens, search spans the filter grid; status/time remain side by side and
actions wrap. Inputs and draft text use (16px) at the existing (620px) breakpoint.
Reuse the incumbent palette, controls and divider surfaces without new global tokens.
The official vendor comparison and product inferences are in
docs/market-research-2026-11.md; the comparison does not establish customer demand.

## Scheduling

Open a flat paper composer within Meetings. Ask for title, date, start time and
duration first. Keep the currently selected time zone visible below the time fields;
the caption updates when the controlled time-zone field changes. Use native browser
date/time fields. More options contains time zone, notes and calendar reminder.

When Google Calendar is connected, the explicit invitation checkbox reveals teammate
emails and changes submission to Schedule & invite. Explain workspace access beside
email entry. Confirmation shows the saved room, time, Join and Copy link. Calendar
failures distinguish the saved meeting from unconfirmed invitations and offer Retry
calendar invitations. Busy states prevent duplicate submission; errors retain the
form. Opening the composer focuses its heading, and Cancel returns focus to Schedule.

## Joining and participating

The room route keeps All meetings and Copy meeting link above a divided prejoin area.
Brief guidance sits beside labeled name and media choices, with a full-width Join
action. Microphone defaults on; meeting camera defaults off. Huddles state that cameras
stay off while screen sharing remains available. Media opens only after Join.

After joining, an ink media stage uses Figtree and a scoped lavender SDK accent within
the cream workspace. Connection status and People sit above the participant media.
Keep microphone, camera where permitted, screen sharing, Chat and Leave controls
visible. Icon controls retain explicit accessible names as their states change.
Screen sharing uses the browser chooser. Chat is live rather than a saved transcript.
Leaving returns to prejoin with a rejoin notice; navigation disconnects the room.

Below (620px), scheduling places date and time side by side, duration across the next
row, and submission across the width. Room rows and prejoin content stack; editable
fields use (16px) text. The active call uses the remaining viewport height, with its
status and control bars outside the flexible media area. Long participant lists scroll
within their popover.

## States and constraints

Show loading, retryable connection errors, unavailable calling and permission/device
failures plainly. Scheduling remains available when calling is not configured. An
unavailable presence service remains unknown. Room links require the same company
workspace. Preserve server-enforced huddle camera restrictions, workspace isolation,
explicit media consent and separate recorder workflows. Deployment and calendar
configuration belong in docs/in-app-meetings.md, outside the ordinary task path.

## Evidence and limits

The final `calls_finish_reviewer` disposition was SHIP; its time-zone-caption finding
was resolved with a controlled caption and a persisted-zone assertion. The seven
validated captures are `.impeccable/review/schedule-desktop.png`, `schedule-mobile.png`,
`call-prejoin-desktop.png`, `call-prejoin-mobile.png`, `huddle-screen-desktop.png`,
`huddle-mobile.png` and `video-meeting-desktop.png`.

The isolated real API/PostgreSQL/LiveKit browser suite exercised two participants with
fake microphone/camera sources and a generated canvas share. It verified received
audio, meeting camera and shared-screen video, chat, huddle camera restrictions,
mobile control placement, eventual provider presence of two, peer cleanup after Leave,
and saved scheduling in Asia/Kolkata. No private media was captured. The backend suite
finished with 394 passed and one skipped, including forced database-policy checks for
room privacy. Existing account/motion and roadmap/voice checks also passed.

These checks do not establish physical-device behavior, cross-network/TURN behavior,
load capacity or actual Google invitation delivery. The finished components and local
styles preserve the incumbent palette, typography, flat surfaces and readable controls;
no material global design-system drift was identified in that comparison.

The later UI-only refinement passed `test:simple-ui` at desktop/mobile widths with
mocked API and speech recognition, covering tab persistence, notes/detail return and
huddle/prejoin return without opening devices. Its captures are
`.impeccable/review/simple-meeting-notes-{desktop,mobile}.png` and
`simple-huddles-{desktop,mobile}.png`. The scoped final correction review returned
SHIP for Home's notes/totals order; it did not re-review call media behavior. The
real API/PostgreSQL/LiveKit evidence above was not rerun for this UI refinement.

The SSO/library extension passed a scoped final correction review after fixing the
sole finding: edits now survive recap/follow-up format switching. This disposition
covers that scope, not the whole product. Latest captures are
`.impeccable/review/meeting-library-{desktop,mobile}.png` and
`meeting-follow-up-{desktop,mobile}.png`. `test:meeting-library` exercised mocked
records in Chromium at (1440px) and (390px): search/reset, exact full-transcript
download, independent edited drafts across formats, copy/download, denied-clipboard
recovery, no overflow and no page errors. The frontend build and existing browser,
account/motion and simple-UI suites passed; the expected lazy CallRoom chunk warning
remains. Twenty-four backend checks with a separate real PostgreSQL database covered
filters, count/pagination, literal wildcards, query bounds, company scope and existing
JWT verification. A read-only filtered request to the main local API returned 200.
No live identity provider, full production stack or real customer demand was verified.
