# Desktop recorder direction

Mode: Operate. Capture a consented desktop call and carry its transcript, notes and
follow-up into the existing workspace. The standalone Electron recorder supports
Microsoft Teams, Slack huddles, Discord calls and other desktop audio on macOS and
Windows; the browser extension remains available for tab capture.

## First viewport

Use the incumbent cream canvas, AOI wordmark and editorial regular/italic heading.
Keep **Open workspace** in the header, followed by explicit recorder state, duration
and labeled call-app, meeting-title, optional-project and listening-language controls.
The microphone is optional; **Keep a local WAV recording** is enabled by default.
Names and technical terms provide vocabulary hints. Explain that the call-app
selector labels the session: system audio includes other apps and notifications,
and video is discarded.

Reserve a separate, always-visible bottom recording dock containing **Start
recording** / **Stop & finalize** and the platform shortcut hint. The main content
scrolls independently; the dock must not cover settings, fields or feedback. Compact
windows stack the form fields while keeping the recording control reachable.

## Interaction

Start only on an explicit, consented button or shortcut action. Default shortcuts
are Command+Shift+Space on macOS and Ctrl+Shift+Space on Windows, configurable in
connection settings. AOI Desktop must be running. Closing its window hides it while
capture continues; recorder and tray/menu-bar states communicate recording. There
is no automatic login launch or background recording start.

Connection and shortcut settings collapse below the capture flow. Save changes
before **Check saved connection**; the label and adjacent guidance identify that
the check uses saved settings. Keep backend/dashboard URLs, authentication mode,
workspace ID and credential controls explicit. Saved credentials use encrypted OS
storage, and dashboard links carry no credential. Audio permission guidance and
the recordings-folder action remain separately expandable.

During capture, show incoming audio level, silence/clipping feedback and the live
transcript when available. Stop flushes audio and requests finalization. Saved
feedback distinguishes local WAV preservation from confirmed live notes and provides
the appropriate meeting/folder actions. An uncertain finalization directs the user
to inspect the workspace before repeating an upload or recording.

## States

Distinguish ready, preparing, recording, reconnecting, recording locally, finishing,
saved and failed states with text. Keep status announcements on the changing status
label; the independently updating duration must not repeatedly announce the whole
status row. Preserve visible alerts, keyboard focus, readable functional captions
and reduced-motion behavior. Permission denial creates no live session. Local WAV
can continue through backend failures when enabled; with backup off, access denial
or exhausted buffering stops capture with an explicit error. Do not imply local-only
recording is live transcription. Local files stay until removed and are never
uploaded automatically.

## Constraints and evidence

Inherit DESIGN.md and .impeccable/design.json unchanged: cream, charcoal, lavender,
flat surfaces, fine dividers, Figtree controls and EB Garamond headings. The dock,
desktop sizing and local recorder aliases are surface behavior, not new global
tokens. Existing roadmaps, Talk to AOI and field dictation remain separate workflows.
No global visual-system change was needed or recorded by this handoff.

Reviewed source: desktop/ui/index.html, styles.css, renderer.js, desktop/main.cjs,
frontend/src/components/ExtensionSetup.tsx and docs/desktop-capture.md. Visual
evidence in .impeccable/review includes desktop-recorder.png,
desktop-recorder-compact.png, desktop-recorder-settings.png and
recording-apps-desktop.png / recording-apps-mobile.png. The finish review resolved
saved-connection clarity, status/timer live-region behavior and functional-caption
readability in one fix batch, then confirmed three recaptures.

Real Electron synthetic-audio checks establish pipeline delivery/finalization,
local WAV, offline fallback and denial behavior. They do not establish native OS
capture permissions, physical shortcut delivery or real-call capture. Windows
packaging on macOS is not a Windows native-audio runtime test. See
docs/desktop-capture.md for setup, retention, recovery and platform limits.
