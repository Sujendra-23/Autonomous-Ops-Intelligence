# Home and workspace direction

Mode: Operate. Scope: “make UI simple to use” within the user's established Wispr
Flow-inspired restyle. Preserve routes, features, authentication, workspace isolation
and recorder handoff. This code-led refinement introduces no new global tokens or
visual world. DESIGN.md and .impeccable/design.json remain unchanged visual authorities.

## Composition and navigation

Keep the cream canvas, charcoal sidebar, lavender selection, locally hosted Figtree
controls and EB Garamond headings. Home uses a compact left-aligned introduction
(52px desktop, 40px below 620px), with primary Open meetings and secondary Upload
transcript. This local variant in `simple-workspace.css` intentionally supersedes the
older centered welcome, capture strip and waveform composition recorded for the
dashboard in DESIGN.md and its sidecar.

Recent meeting notes lead the work area. A supporting rail appears only for nonzero
attention items or open tasks. Workspace totals follow the notes in DOM and visual
order and are omitted when all counts are zero. Mobile stacks notes, the optional
rail, then totals. Preserve filtering, source evidence, honest loading/offline states,
retry and an explicit opt-in example preview that never writes workspace records.
The empty ledger offers recording setup; the primary Recording apps entry retains
the full setup path. Remove repeated capture promotion, connector promotion and
footer framing so work is easier to reach; connector configuration remains in Settings.

The five primary entries are Home, Meetings, Tasks, Roadmaps and Recording apps.
Expandable Reports contains Decisions, Search & insights and Weekly summary and opens
for an active report. Settings and Login/Logout remain at the bottom. Account adds
Plans & pricing. Workspace identity is static rather than a misleading settings
switcher. The sticky header keeps current location, Search, Talk to AOI and Account.
The existing focus-managed mobile drawer, visible focus, scroll flow and reduced-motion
behavior are retained. Account details remain in surface-account-access.md.

## Evidence and limits

Final review first requested only that notes precede totals. After correcting DOM and
visual order, the scoped correction review returned SHIP: the sole fix was resolved,
with no introduced regression visible in that scope. This is not a broad re-review
of all features.

Desktop/mobile captures are `.impeccable/review/simple-home-empty-{desktop,mobile}.png`,
`simple-home-populated-{desktop,mobile}.png` and `simple-navigation-mobile.png`.
The frontend build, `test:browser`, `test:account-motion` and `test:simple-ui` passed;
the lazy media chunk warning remains expected. The new UI suite uses mocked API and
speech-recognition responses at desktop/mobile widths, covering notes-first order,
navigation/report/pricing access, meeting-tab refresh/back, huddle prejoin/back and
local spoken navigation, with no page errors or horizontal overflow. It performs no
real workspace writes or media capture. Existing call/backend evidence was not rerun
for this UI refinement; meeting routes and test setup are in docs/in-app-meetings.md.
