# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Product Purpose
AOI turns meeting transcripts into tasks, decisions, risks, and blockers. A Chrome extension captures live tab audio; a standalone Electron companion captures desktop call audio on macOS and Windows. The web dashboard supports review and follow-up using the same backend and workspace.

## Capabilities and Constraints

Live captures support automatic multilingual recognition or an explicit listening
language. Store the preference per meeting, retain it across reconnects, and keep
transcripts/source evidence in the languages spoken. Provider coverage varies;
do not promise every language works in every automatic mode.
The existing React/Vite dashboard includes meeting history, transcript detail, editable tasks, decisions, semantic search, drift detection, weekly summaries, workspace access, and connectors. Preserve API authentication, workspace isolation, routes, and extension handoff. The backend may be offline locally: present connection state honestly and never invent workspace records. Optional example data must be explicitly labeled and must not trigger writes.

The user selected PostgreSQL with pgvector as the primary database and requested integration with their Multi-Tenant Identity Access Gateway. Company sign-in uses verified gateway identity and live-session checks; a company maps to one isolated workspace shared by its users. Connector configuration and meeting records remain company-specific. Gateway companies/members are managed by that service; AOI handles its workspace name and tools. Company setup is configuration-driven and not enabled by silently replacing development credentials.

## Brand Commitments
The user requested a Wispr Flow-inspired visual direction and confirmed “Restyle the existing app.” Use a cream canvas, charcoal navigation, lavender accents, Figtree interface text, and editorial EB Garamond headings while preserving the existing operational workflows. This replaces the earlier forest/citrus direction. The extension remains a separate capture surface, named **AOI extension** as confirmed by the user.

## Pricing
User-confirmed: Pro costs $50 per company per month for 10–20 members. Team is for larger companies with custom monthly pricing based on company size, arranged through a conversation. Checkout, final feature entitlements, and usage allowances are not implemented or confirmed. A sales contact has not yet been supplied.

## Evidence on Hand
Product facts and existing workflows are documented in README.md and docs/extension-dashboard.md. Manrope is already bundled with the extension under the SIL Open Font License.
Desktop setup and capture limits are documented in docs/desktop-capture.md. Transcription review, provider behavior and the bounded evaluation result are documented in docs/transcription-quality.md and docs/transcription-evaluation.json.

## Open Decisions
Specific audience and organization branding have not been supplied. Do not invent customer names, pricing, performance claims, or a signed-in identity.

## Unified task tracking
Companies can use AOI as a standalone task tracker: native tasks have a title, description,
owner, due date, priority and status, with no LLM key or connector required. Tasks captured
from meetings and imported Jira/Linear issues share this list. Connectors are optional;
existing Jira project and Linear team issues are imported through bounded, resumable scans.
Imported issue metadata remains authoritative in its original tool; linked status changes
use the existing optional two-way status sync. Native tasks are not automatically exported.

## Project roadmaps and speech control
Roadmaps propose an editable goal, milestones and tasks from a written plan, one completed
meeting transcript, or up to five recent completed meetings scoped to the selected project.
Save versioned roadmaps to workspace projects, optionally creating linked native tasks
(enabled by default). Milestone status is manual and separate from task status. Generation
requires the configured AI provider; review proposed timing, scope and assumptions before saving.

Talk to AOI is available from the topbar for spoken or typed commands, including navigation
and creating a roadmap with its goal, milestones and tasks through current UI controls.
Microphone recognition starts only on explicit action and depends on browser support and
permission. Field dictation, language selection and optional spoken replies are supported.
The planner uses bounded current-screen context and allowed actions; credential fields and
file selection are excluded. Browser speech services may process audio, and semantic
commands use the workspace AI provider. See docs/roadmaps-and-speech.md for setup, recovery
and verification limits; live microphone, provider quality and PostgreSQL integration have
not been established by the mocked browser checks.

## Desktop capture and transcript review

AOI Desktop records system audio for Microsoft Teams, Slack huddles, Discord calls
and other desktop calls, with an optional microphone. The call-app selector labels
the session; it does not isolate one application's audio. System capture includes
other apps and notifications. Video is discarded. Capture starts only through an
explicit, consented button or shortcut action while AOI Desktop is running; it does
not start at login or record automatically. The configurable default toggle is
Command+Shift+Space on macOS and Ctrl+Shift+Space on Windows. Visible recorder and
tray/menu-bar states identify active capture. macOS requires 14.2 or newer and OS
recording permissions.

Local WAV backup is enabled by default and can preserve audio when live notes are
offline. Files remain in the application's recordings folder until removed and are
not uploaded automatically. Live transcription still requires the configured backend,
workspace authentication and speech provider. Saved desktop credentials use encrypted
OS storage; dashboard links do not carry them. Development packages are available
for both platforms; distribution signing requires the owner's platform credentials.

Listening language, bounded names/technical-term hints, audio-level warnings, ordered
PCM delivery and final-result flushing improve the transcription path. Flagged
passages in the dashboard support review against the recording while retaining the
full transcript. Confidence is a provider-dependent heuristic, never a guarantee
that flagged or unflagged words are correct; unavailable scores remain unknown.
No error-free or universal-language accuracy claim is supported. Existing Talk to
AOI and field-dictation controls retain their separate behavior.

Real Electron checks with synthetic audio cover the recording pipeline, local backup,
offline fallback and permission denial. Native OS permission handling, physical
shortcut delivery and actual Teams/Slack/Discord capture still need device acceptance;
Windows native audio has not been runtime-tested on the Mac host. One 12.31-second
synthetic English OpenAI evaluation preserved its critical names, date, amount, owner
and negative commitment. Raw WER was 0.2069 with case/punctuation normalization because
numeric/date formatting counts as word edits. This is not a real-call, multilingual
or noisy-room accuracy benchmark.

## Account access and scroll flow

Account in the workspace header opens Workspace settings and Logout for authenticated
sessions, or Login for local development. The sidebar provides the same session entry.
The `/login` route uses the configured gateway or OIDC provider. Without configured
authentication it explains that account login is unavailable and offers a return to
the local workspace; it does not accept pretend credentials. Configuration remains
explicit and does not provision company accounts or a database.

Gateway logout waits for an in-flight session refresh, requests server revocation,
then clears the in-memory bearer, workspace selection and API key, removes the saved
company marker, and returns to login. Failed revocation offers retry. Talk to AOI's
explicit logout command uses the same sign-out action. Gateway tokens and passwords
are not saved in web storage; the existing OIDC session-storage behavior is unchanged.

A slim header line follows page position, and newly reached work sections unfold
once per view, including sections loaded later. Content is visible by default.
Reduced motion hides the line and cancels section movement; Back to top remains
available. Isolated Chromium checks with mocked identity and API responses cover
desktop/mobile account flows, logout recovery, company-session credential cleanup,
local mode, command-handler logout and motion behavior. They do not establish live
gateway/OIDC, database isolation, microphone accuracy or scroll smoothness benchmarks.
See docs/account-access-and-motion.md for setup and verification details.
