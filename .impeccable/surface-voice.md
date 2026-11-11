# Talk to AOI direction

Mode: Operate. Add a workspace-wide way to execute spoken or typed commands against the current interface, including **Go to Roadmaps** and creating a goal, milestones and tasks. A second mode dictates into a previously selected text field.

## First viewport

Place **Talk to AOI** in the topbar with the other global controls. Its lavender pill becomes an icon control at compact widths while retaining an accessible name. Opening it reveals a bounded, scrollable paper panel below the header. The initial panel shows a short command example, **Do an action** / **Dictate into a field** mode buttons, a labeled command textarea, explicit listening/run actions, status feedback and collapsed speech settings/privacy. Keep the launcher and panel clear of workspace save controls.

## Interaction

Opening moves focus to the command field; closing, including Escape from the panel, cancels recognition and further command steps and restores focus to the launcher. **Start listening** is the only microphone start action. **Finish & run** ends command recognition and executes the recognized text; typed **Run command** uses the same command path. Simple navigation runs locally. Semantic commands use the configured provider to plan allowed navigation, click, fill and select actions against the current bounded snapshot, then recheck resulting screen feedback between steps. Show progress; **Stop** cancels further steps, while an already sent action may still finish.

For dictation, select an eligible app text field before opening the panel. **Finish dictation** appends recognized words to that field and updates its form state. Speech settings select the recognition language and opt into reading replies aloud. Mode changes are disabled while listening or working; language changes are disabled while listening.

## States

Distinguish ready, listening, working, stopped, completed and failed states with explicit text and status/alert announcements. Explain unavailable recognition and retain typed commands. Give actionable microphone-denied, no-speech, missing dictation target, provider/network and changed-screen feedback. Stop on ambiguous or unavailable targets, processing errors, cancellation and the step limit. Completion must reflect resulting UI feedback; after an uncertain request, direct the user to check the screen before repeating it. Keep panel content reachable by scrolling on small screens, use readable mobile text fields, and preserve focus outlines.

## Constraints and evidence

Inherit DESIGN.md and .impeccable/design.json unchanged: cream/paper surfaces, lavender launcher, charcoal controls, Figtree operational text, fine borders and existing focus treatment. This utility extends the incumbent visual system without new global tokens. It is an action layer over allowed current UI controls, not background automation. Exclude credential/password/token/secret fields and file selection; require direct entry and the browser file picker for those actions. Browser speech services may process audio. The workspace AI provider receives the utterance and bounded current-screen context after existing DLP redaction. Preserve authentication, workspace boundaries and request limits.

Isolated browser checks exercised compound commands, task/roadmap saves and field dictation with simulated recognition and mocked API/provider responses on desktop/mobile. Launcher placement and keyboard entry/restoration were corrected after initial review; final reviewer disposition remains separate. Live microphone accuracy, provider quality and a real PostgreSQL round-trip are unverified. See docs/roadmaps-and-speech.md for operational setup and recovery.
