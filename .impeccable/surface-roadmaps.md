# Roadmaps direction

Mode: Operate. Extend the existing workspace with project roadmaps generated from project meetings, one completed transcript, or a written plan. The task is to turn a goal into a reviewable sequence of milestones and native workspace tasks, then revisit and update the saved plan.

## First viewport

Use the incumbent shell and editorial page heading above a divided project picker. A new project opens the paper composer: project name, three explicit source choices, goal/milestone/task guidance, planning horizon and optional start date lead to **Generate roadmap**. Project meetings require an existing project; transcript choices show completed meetings scoped to the selected project. Source guidance makes the five-meeting limit visible. A saved project shows its roadmap with the composer collapsed into **Plan a new version**.

## Interaction

Generation creates a draft, closes the composer and moves keyboard focus to the roadmap. The document presents its summary, assumptions, version and numbered vertical milestone sequence with dependency notes. Edit milestone titles, outcomes, owners, week ranges and tasks before saving. Add/remove milestone controls and explicit status selectors remain ordinary labeled controls. **Save roadmap & tasks** creates missing linked native tasks by default; the task-creation checkbox permits saving only the roadmap. Existing linked tasks are reused, and changing a draft does not delete previously created tasks. Milestone status is manual and independent of task completion.

Keep unsaved changes visible: save or discard before switching projects, retain the draft after a failed save, and warn before leaving the browser page with dirty changes. Saved versions use conflict checking; reload a conflicting saved roadmap before editing again. Success copy reports the save and newly created task count. Schedule and task validation explains what needs repair before saving.

## States

Provide a useful empty introduction, no-completed-meetings guidance, loading/generating/saving labels, disabled controls while busy, a status announcement for successful generation/save and an alert with connection retry for errors. Provider or connection failures retain the current plan. Proposed scope and timing remain reviewable assumptions. Long names and task text wrap; source buttons and project controls wrap on narrow screens, fields stack, and the milestone rail narrows without losing labels.

## Constraints and evidence

Inherit DESIGN.md and .impeccable/design.json unchanged: cream canvas, flat paper containers, fine dividers, lavender selection, charcoal primary actions, Figtree forms/data and EB Garamond headings. The roadmap composition and local responsive rules are surface behavior, not new system tokens or a new visual world. Preserve workspace isolation and source scoping. Native tasks are not automatically exported to connectors. Migration and provider setup are documented in docs/roadmaps-and-speech.md.

Isolated browser checks exercised manual, transcript and project sources, editing and task saves on desktop/mobile with mocked APIs. Backend checks cover source scope, validation, version conflicts and atomic task creation. These checks do not establish a real PostgreSQL round-trip or live provider quality. Final design-review disposition is recorded separately when available.
