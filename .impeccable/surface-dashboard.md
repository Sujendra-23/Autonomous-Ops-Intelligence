# Dashboard direction

Mode: Operate. Confirmed scope: “Restyle the existing app.” The user's Wispr Flow reference supersedes the earlier forest/citrus direction. Implement in the established React app, preserving routes, data flows, authentication, workspace isolation, and extension handoff.

Use a cream canvas, charcoal sidebar, lavender selection and prominent upload actions, locally hosted Figtree, and regular/italic EB Garamond. The dashboard centers its large two-line welcome and supporting actions above a compact black capture strip with a cream waveform capsule. Divided counters lead into a paper meeting ledger and lavender attention rail. Keep ordinary primary buttons black and pill-shaped.

Show real workspace counters and records when connected. Offline and empty states retain actionable setup and connection retry. Optional example records appear only after choosing the explicitly labeled preview and never trigger writes. Product capabilities and pricing remain as recorded in PRODUCT.md.

Signature interaction: a finite 27-bar waveform (1400ms, three iterations), disabled under reduced motion. Meeting filters, example previews, retry, upload, task controls, and navigation retain keyboard and pointer behavior. Mobile uses the existing focus-managed drawer and a single reading column; capture content wraps with a full-width extension action.

Scroll flow extends this composition with a slim plum line beneath the sticky header
and a bounded, once-per-view unfolding of newly reached work sections (480ms).
Content remains visible by default; sections loaded later join the same flow. Passive
scroll sampling schedules at most one animation frame, and view changes clean up
listeners, observers and active animations. Reduced motion hides the progress line
and cancels section movement. Back to top appears after (500px), restores main-content
focus, and returns immediately under reduced motion. Its visibility initializes
again on every view change.

Implementation is code-led. The current visual system is documented in DESIGN.md and .impeccable/design.json. Review covered reference, desktop/mobile dashboard, upload, tasks, and labeled example screenshots in .impeccable/review; final reviewer disposition was SHIP with no material findings. Frontend build passed; ten routes at six widths had no horizontal page overflow, and keyboard focus interactions were checked.

Account and scroll-flow additions received a fresh SHIP review from
`account_motion_finish_reviewer`, with no material fixes. Final scroll evidence is
in `.impeccable/review/scroll-flow-desktop.png` and `scroll-flow-mobile.png`.
Mocked Chromium checks cover animation invocation, progress updates and reduced
motion at desktop/mobile widths; they are not a scroll smoothness benchmark.
Account composition and session limits are recorded in surface-account-access.md.
