# Project roadmaps and speech control

Roadmaps turn a written project plan, one completed meeting transcript, or up to five
recent completed project meetings into an editable proposal. Plans can include a goal,
explicit milestone titles, tasks, owners and timing. When milestones/tasks are supplied,
the prompt instructs the model to preserve that structure and wording. Other timing and
scope are proposals and assumptions to review, not promises.

Open **Roadmaps**, select an existing project or name a new one, select a source, and
choose **Generate roadmap**. Edit milestones, outcomes, owners, week ranges and tasks.
**Save roadmap & tasks** saves the roadmap and creates missing native AOI tasks linked
to its project. Uncheck task creation to save only the roadmap. Existing linked tasks
are reused; matching roadmap task titles within the same milestone are reused after
regeneration. Changes do not delete previously created tasks. Roadmap milestone status
is tracked separately from individual task status. Native tasks are not automatically
exported to Jira or Linear.

Project roadmaps are stored on the existing workspace-scoped project row. Saves use a
version check and row lock so two editors cannot silently overwrite each other. Task
creation and the roadmap save share a transaction. Reload a conflicting saved roadmap
before making another edit; unsaved drafts are retained after failed saves.

## Setup

Apply the additive migration before serving the updated backend:

```sh
cd backend
alembic upgrade head
```

The new revision is `0010_project_roadmaps`. Docker deployments apply migrations using
the existing startup flow. Generation and semantic voice commands use the configured
`LLM_PROVIDER`, model and provider API key already used for meeting extraction. No new
credentials are embedded in the frontend. Without a configured provider, these operations
show an error and retain the current plan. Existing workspace roles, ingestion key checks,
request rate limits, paid-operation quotas and DLP redaction apply.

## Talk to AOI

The **Talk to AOI** control is available throughout the workspace. Choose **Start
listening**, speak a command, then **Finish & run**. Recording starts only after that
explicit action; closing the panel cancels recognition. Browser speech services may
process microphone audio. The panel explains this and offers language selection and
optional spoken replies. Speech recognition depends on browser/OS support and microphone
permission; typed commands remain available when recognition is unavailable.

Examples:

- “Go to Roadmaps.”
- “Create a Customer Portal project. The goal is to launch onboarding. Milestones are
  Prototype and Release. Prototype tasks are Build the prototype and Review it.
  Release task is Publish the release. Create the roadmap and these tasks.”
- “Open Tasks and create a task called Review the onboarding prototype.”

For field dictation, first focus a text field, open Talk to AOI, choose **Dictate into a
field**, then start listening. Recognition appends text to that field and updates React's
form state. Credentials and file selection are excluded: enter credentials directly and
use the browser file picker for local recordings.

Simple navigation commands run locally. Other commands are planned against the current
screen's labeled controls, then executed through the same UI actions as pointer or
keyboard use. The assistant rechecks the screen between steps, shows progress and stops
on errors, unavailable targets, ambiguity, cancellation or its step limit. It does not
claim a mutation is complete before checking resulting screen feedback. **Stop** stops
further command steps; a request already sent may finish, so check the current screen
before repeating it. Avoid repeating a command after an uncertain network failure until
you have checked for the resulting records.

The provider receives the utterance and bounded current-screen context after DLP
redaction. The control list excludes password, API key, token and secret fields. Commands
cannot select arbitrary external URLs or invented controls. File selection still needs
user interaction. The command planner follows the current UI; it is not an unrestricted
agent or background automation service.

## API

- `POST /api/projects/roadmap/generate`: read source records and generate a validated draft;
  no database writes.
- `GET /api/projects/{project_id}/roadmap`: load the saved roadmap and version.
- `POST /api/projects/roadmap`: create/update the roadmap with `expected_version` and optional
  `create_tasks`.
- `POST /api/assistant/command`: plan allowed UI actions against a bounded control snapshot;
  this endpoint does not perform database writes itself.

OpenAI generation follows the existing Chat Completions JSON-mode integration, with
server-side Pydantic validation because JSON mode alone does not enforce a schema.
See the [official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat).
The existing Anthropic provider is also supported.

## Verification

Backend tests cover source scoping/completion, bounded context, malformed output,
dependency scheduling, provider truncation, redaction, authorization, atomic task creation,
repeat saves, version conflicts and allowed command controls. Browser checks use isolated
mock API/provider responses and simulated recognition to exercise manual/transcript/project
sources, editable drafts, task saves, status updates, compound spoken commands and field
dictation on desktop and mobile. They do not establish live microphone accuracy, provider
quality or a real PostgreSQL round-trip when those services are unavailable.

Run the feature checks with `pytest tests/test_roadmaps.py` from `backend`. For browser
checks, run the Vite dev server, then `npm run test:browser` from `frontend`. Install
Playwright's browser once with `npx playwright install chromium` if needed. The browser
test intercepts API requests and uses a simulated recognition engine; it creates no live
workspace records.
