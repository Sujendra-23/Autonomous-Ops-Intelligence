---
name: AOI
description: Cream and lavender workspace for meeting intelligence
colors:
  bg: "#ffffeb"
  panel: "#fffef6"
  panel-2: "#f0f0e2"
  border: "#deded0"
  text: "#1a1a1a"
  muted: "#62625a"
  accent: "#1a1a1a"
  accent-2: "#69477d"
  lavender: "#f0d7ff"
  forest: "#1a1a1a"
  citrus: "#f0d7ff"
  forest-muted: "#c3c3b7"
  warn: "#79541e"
  crit: "#a13c35"
  ok: "#24604d"
  focus: "#80509a"
  button-hover: "#393932"
  lavender-hover: "#e6c6f8"
typography:
  display:
    fontFamily: "EB Garamond, Georgia, serif"
    fontSize: "clamp(58px, 5.8vw, 84px)"
    fontWeight: 400
    lineHeight: 0.94
    letterSpacing: "-0.035em"
  display-accent:
    fontFamily: "EB Garamond, Georgia, serif"
    fontSize: "1em"
    fontWeight: 400
  headline:
    fontFamily: "EB Garamond, Georgia, serif"
    fontSize: "44px"
    fontWeight: 400
    lineHeight: 1.1
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Figtree, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Figtree, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Figtree, sans-serif"
    fontSize: "12px"
rounded:
  field: "6px"
  control: "8px"
  tile: "10px"
  inset: "12px"
  panel: "14px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  xl: "24px"
  section: "32px"
  page: "40px"
components:
  button-primary:
    backgroundColor: "{colors.text}"
    textColor: "{colors.bg}"
    rounded: "{rounded.pill}"
    padding: "10px 19px"
  button-primary-hover:
    backgroundColor: "{colors.button-hover}"
  button-lavender:
    backgroundColor: "{colors.lavender}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "10px 20px"
  button-lavender-hover:
    backgroundColor: "{colors.lavender-hover}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.pill}"
    padding: "9px 14px"
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.text}"
    rounded: "{rounded.field}"
    padding: "8px 10px"
    typography: "{typography.body}"
  nav-active:
    backgroundColor: "{colors.lavender}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "12px"
  status-completed:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.muted}"
    rounded: "5px"
    padding: "4px 7px"
    typography: "{typography.label}"
  ledger:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "22px 24px 0"
  capture:
    backgroundColor: "{colors.text}"
    textColor: "{colors.bg}"
    rounded: "{rounded.panel}"
    padding: "17px 22px"
---

# Design System: AOI

## Overview

**Direction: Wispr Flow-inspired workspace.** The user confirmed a restyle of the existing app. Cream, charcoal, lavender, and large editorial serif headings now define the interface; this replaces the earlier forest/citrus system.

Figtree keeps operational detail compact and readable. EB Garamond gives introductions a regular-and-italic cadence. Flat paper surfaces, fine dividers, and restrained accent areas make room for dense meeting and task records. The dashboard's centered welcome and compact waveform strip are its surface composition, not a required layout for every screen.

**Key Characteristics:**
- Cream canvas, charcoal navigation, and lavender selection.
- Expressive serif headings with practical sans-serif controls and data.
- Flat ledgers, tonal grouping, visible focus, and finite signal motion.

## Colors

Frontmatter values are normative and extracted from the final cascade in `frontend/src/components/studio.css`.

### Primary
- **Charcoal ink** (`text`, `accent`): main text, ordinary primary buttons, sidebar, and capture strip.
- **Soft lavender** (`lavender`): selected navigation, the dashboard upload action, and the topbar action. Use its hover token for interactive feedback.

### Secondary
- **Plum** (`accent-2`): secondary categories and supporting accent text; pale lavender groupings distinguish attention and connector areas.

### Tertiary
- **Ochre** (`warn`), **brick** (`crit`), and **deep green** (`ok`): warning, critical, and success roles. Always pair status color with readable state text.

### Neutral
- **Cream** (`bg`): main canvas and light text on ink.
- **Paper** (`panel`): ledgers and ordinary containers.
- **Inset cream** (`panel-2`): fields and quiet inset surfaces.
- **Soft divider** (`border`): container edges and row separation.
- **Muted ink** (`muted`): supporting copy; **sidebar muted** (`forest-muted`): light secondary text against charcoal.
- **Plum focus** (`focus`): keyboard outline.

**The Compatibility Rule.** Existing `forest` and `citrus` names remain code aliases for charcoal and lavender. They do not authorize restoring green or citrus styling.

## Typography

**Interface font:** locally hosted Figtree, regular, medium, semibold, and bold. **Display font:** locally hosted EB Garamond, regular and italic. Both have bundled Open Font License notices.

Display headings use the serif throughout, with italic emphasis at the same size. Navigation, forms, tables, statuses, and task content use Figtree. Transcript source textareas keep their monospace treatment.

The frontmatter display role describes the desktop welcome. Its responsive sizes are (70px) at 1250px, (64px) at 1050px, (72px) below the 800px drawer breakpoint, and `clamp(48px, 14vw, 64px)` below 620px. Ordinary page headings use the headline role, with mobile reduction to (36px); individual work surfaces retain local compact variants. Metric values use regular EB Garamond at (38px), reducing to (34px) on mobile. Compact helper text commonly uses (12px), while welcome supporting copy uses (15px).

**The Reading Rule.** Keep serif expression in headings and metric values; keep operational detail in Figtree.

## Layout

The desktop shell has a sticky, full-height charcoal sidebar (232px) and a flexible content column. Main content uses horizontal padding (40px); the sticky topbar is at least (76px) high. Workspace pages generally cap at (1200px), tasks at (1320px), upload at (920px), and pricing at (1120px).

The dashboard centers its welcome above a horizontal capture strip and divided counters. Its meeting ledger sits beside a supporting rail (272px) with a gap (24px). Tasks use source tabs, filters, and one shared ledger; upload uses source controls and labeled metadata. These are surface layouts, not global templates.

At (1250px), the sidebar narrows to (218px), content padding to (28px), and dashboard rail to (235px). At (1050px), the ledger and rail stack, with rail sections side by side. At (800px), navigation becomes a fixed drawer (248px), and the topbar reduces to (64px). At (620px), content padding becomes (18px), rail sections stack, capture content wraps, and task rows become labeled mobile blocks. Pricing stacks at (1100px); task controls also adapt at (1000px). Above (1650px), page and topbar horizontal padding becomes (56px).

## Elevation & Depth

The studio is flat at rest. Tonal backgrounds and one-pixel borders separate containers; ordinary cards and ledgers have no ambient shadows. The mobile drawer uses a translucent backdrop. Global keyboard focus is a clear outline (2px) with offset (4px), with local inset offsets where needed inside clipped containers.

## Shapes

Ordinary primary and ghost buttons are pills; prominent lavender upload and topbar actions use the control radius. Fields have tighter corners, content panels have softer corners, and avatars are circular. Status chips use compact corners (5px). Keep operational rows aligned and borders fine.

## Components

### Buttons

Ordinary primary actions use ink on cream contrast, Figtree at (13px), weight (500), and minimum height (40px). Their hover uses the charcoal hover token. Ghost actions have a fine muted border and minimum height (38px). The dashboard upload action uses outlined lavender with minimum height (44px); the topbar version is compact at (36px). Disabled controls retain explicit unavailable behavior and muted opacity. Keep accessible names when mobile hides text.

### Chips

Completed meeting chips use cream with muted text. Warning, failed, task-source, and workflow chips retain distinct category treatments and explicit labels. Meeting filters use a black selected pill and `aria-pressed`; task-source tabs use an underline. Example context remains explicitly labeled.

### Cards / Containers

Meeting and task ledgers use the paper surface, panel radius, and divider border. Meeting ledgers start with padding (22px 24px), divided rows, and an inset cream footer. Attention, connections, and featured pricing use pale lavender groupings. Do not add shadows to recreate hierarchy.

### Inputs / Fields

Standard fields use inset cream, a divider border, field radius, and the frontmatter padding. Upload and task editor fields use larger internal padding; mobile text entry becomes (16px). Preserve labels, placeholder contrast, caret visibility, and focus outline. Company sign-in pairs a cream introduction with a paper form, stacks at (800px), and uses an explicitly labeled lavender submit action.

### Navigation

Charcoal navigation uses icon-label rows, Figtree at (13px), and minimum height (44px). Default muted text brightens over a darker hover surface. Active rows use lavender with ink and stronger type. The mobile drawer retains its close control, keyboard focus trap, inert background, Escape handling, and focus restoration. The topbar remains sticky while scrolling.

### Capture Signal and Motion

The compact black capture strip places a cream waveform capsule beside concise copy and an outlined pill action. Its (27) decorative bars animate over (1400ms) for three iterations using the shared easing curve, then settle. Keep bars hidden from assistive technology and the extension action labeled. On mobile the action spans the strip width.

Controls transition over (180ms), fields over (150ms), and the drawer over (220ms). The task editor appears over (230ms); a back-to-top control appears after scrolling (500px). Reduced motion disables waveform, drawer, editor, and decorative movement, and returns to top immediately. Content remains visible without animation support.

## Do's and Don'ts

### Do:
- **Do** use the cream, ink, and lavender system across existing app surfaces.
- **Do** preserve readable data, keyboard focus, labeled controls, and reduced-motion behavior.
- **Do** design empty, loading, offline, and explicitly labeled example states with the same care as populated views.

### Don't:
- **Don't** reintroduce the superseded forest/citrus palette through compatibility token names.
- **Don't** apply serif styling to dense data, navigation, or form labels.
- **Don't** present illustrative records as live data or imply unavailable checkout or contact actions work.
