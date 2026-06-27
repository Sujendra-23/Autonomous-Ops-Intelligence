# Autonomous Operational Intelligence Layer

> An **AI Agent for Work** that converts meetings into **structured tasks and
> decisions with zero manual tagging** — and mirrors them into Notion,
> Linear/Jira, and Slack autonomously. **Security is a first-class product
> concern**, not an afterthought.

## Why this exists

Today's meeting-notes AI — including Notion AI Meeting Notes — produces a wall
of summary text. A human still has to read it, decide what's actionable,
**manually tag** the tasks, assign owners, copy them into Linear, paste a recap
into Slack, and remember to chase the follow-ups a week later. The structure
the downstream tools actually need — owner, due date, priority, source
evidence — never makes it out of the summary.

This project closes that gap. It is an autonomous agent that:

- **Extracts structure, not summaries.** Every meeting becomes a typed set of
  tasks, decisions, risks, and blockers — each with an explicit owner, due
  date, priority, verbatim source quote, and confidence score. **Zero manual
  tagging. Zero copy-paste.**
- **Acts in the tools teams already use.** Linear issues, Notion project
  pages, Slack summaries — all created automatically in the same pass.
- **Stays awake after the meeting ends.** A drift monitor catches overdue,
  stalled, unowned, and aged-blocker work and nudges Slack with per-task
  cooldowns so the team doesn't drown in reminders.
- **Treats secrets as a product concern.** Every API key is wrapped in
  `pydantic.SecretStr` so it can't leak into logs, tracebacks, or settings
  dumps; `.env` is auto-created at mode `0600`; and the README ships a macOS
  Keychain recipe so you can keep the key off disk entirely. See
  [Secrets handling](#secrets-handling) — security is treated as something
  the user *sees*, not just something hidden in the implementation.

Drop a transcript in. Walk away. Trackers stay current; the team gets pinged
about the things they forgot.

```
  Transcript ─►  (1) The agent reads it
                       chunk + embed + LLM structured extraction
                 (2) The agent decides what's actionable
                       tasks · decisions · risks · blockers
                       — each with owner, due, priority, source quote
                 (3) The agent acts in the tools you use
                       Notion page  ·  Linear/Jira issues  ·  Slack summary
                 (4) The agent stays awake
                       drift monitor loop · per-task reminder cooldown
                       overdue · stalled · unowned · aged blockers
```

The four phases are intentional — this isn't a pipeline that fires once and
disappears. The agent is a long-lived service that continues to act on the
team's behalf between meetings.

---

## What the agent does

| Capability                              | Status   | Notes                                                          |
| --------------------------------------- | -------- | -------------------------------------------------------------- |
| 🤖 Autonomous transcript ingestion      | ✅       | FastAPI endpoint, optional API-key gating                      |
| 🤖 Zero-tag structured extraction       | ✅       | LLM emits typed tasks/decisions/risks/blockers, no human tags  |
| 🤖 Owner + due-date inference           | ✅       | Names + dates parsed from natural speech, never invented       |
| 🛡 Schema-validated LLM output          | ✅       | Pydantic catches hallucinations before anything is persisted   |
| 🔒 `SecretStr` on every credential      | ✅       | Keys can't leak into logs, traces, or settings dumps           |
| 🔒 `.env` auto-locked to mode `0600`    | ✅       | First-class security UX, not buried in implementation          |
| 🔍 Auditable source quotes              | ✅       | Every extraction carries a verbatim quote + confidence score   |
| 🤖 Notion mirror                        | ✅       | Auto-creates project page + appends each meeting               |
| 🤖 Linear mirror (GraphQL)              | ✅       | One issue per extracted task                                   |
| 🤖 Jira mirror (REST + ADF)             | ✅       | Alternative to Linear                                          |
| 🤖 Slack summaries + nudges             | ✅       | Block Kit summary post-meeting; reminders on drift             |
| 🤖 Drift monitor (always on)            | ✅       | overdue · stalled · missing-owner · aged blockers              |
| 🤖 Semantic recall                      | ✅       | pgvector search across all past transcripts                    |
| 🧠 Cross-meeting memory                 | ✅       | Extractor is fed the project's open tasks/decisions/blockers   |
| 🧠 Task dedup + status updates          | ✅       | Re-stated tasks merge; "X is done" closes the existing task    |
| 🧠 Decision supersession                | ✅       | A reversing decision flags the prior one as superseded         |
| 🎙 Live note-taker (browser extension)  | ✅       | Streams tab audio → STT → notes appear *during* the meeting     |
| 🖥 Agent console (React + Vite)         | ✅       | Dashboard, task review, drift scans, semantic search           |
| Tests                                   | ✅ Pytest | Pure unit + integration (auto-skip without Postgres)           |

Every integration is **off by default** and turns on automatically the moment
you populate its keys in `.env`. You can run the whole pipeline with just an
LLM key and ignore the rest — extraction still happens, it just doesn't
mirror outward.

---

## Architecture

```
                     ┌──────────────────┐
                     │  React + Vite UI │  http://localhost:5173
                     └────────┬─────────┘
                              │ JSON
                     ┌────────▼─────────┐
                     │  FastAPI backend │  http://localhost:8000  (/docs)
                     │                  │
       /api/transcripts ─► ingest, list, reprocess
       /api/projects    ─► rollups
       /api/tasks       ─► list, update, activity log
       /api/decisions   ─► browse
       /api/intelligence/dashboard   ─► counts
       /api/intelligence/drift/run   ─► on-demand scan
       /api/intelligence/search      ─► vector search
                     └────┬─────────────┘
                          │
       ┌──────────────────┼─────────────────────────────────┐
       │                  │                                 │
┌──────▼───────┐  ┌───────▼────────┐  ┌────────▼─────────┐
│ Postgres 16  │  │   Extraction   │  │  Integrations    │
│  + pgvector  │  │   pipeline     │  │  Notion / Linear │
│              │  │ (LLM + chunker │  │  Jira  / Slack   │
│              │  │  + dispatcher) │  │                  │
└──────────────┘  └────────────────┘  └──────────────────┘
       ▲                                       ▲
       │ ticks every N seconds                 │
       │                                       │
┌──────┴────────────────────────────────────────┴────────┐
│             Drift monitor worker container             │
│   overdue · stalled · missing-owner · old blockers     │
└────────────────────────────────────────────────────────┘
```

---

## Quick start (10 minutes)

### 1. Configure env

```bash
cd ~/Desktop/autonomous-ops-intelligence
cp .env.example .env
```

Edit `.env` and set **at least one** of:

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

or

```ini
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

You can leave Notion / Linear / Jira / Slack blank — they're optional. If you
do want those, drop the keys in. See `.env.example` for the full list.

### 2. Bring the stack up

```bash
make up      # docker compose up -d --build
make logs    # tail everything
```

The backend container automatically runs `alembic upgrade head` on boot, so
the database is migrated before the API accepts requests.

What you get:

- **Postgres + pgvector** on `localhost:5432`
- **Redis** on `localhost:6379` (reserved for future queue work)
- **API** on `http://localhost:8000` — interactive docs at `/docs`
- **Worker** running the drift monitor every `MONITOR_INTERVAL_SECONDS`
- **Frontend** on `http://localhost:5173`

### 3. Try it

Open `http://localhost:5173`, click **Upload transcript**, paste the contents
of `examples/sample_transcript.txt`, and ingest.

Or do it via curl:

```bash
make seed
```

Then visit the **Tasks**, **Decisions**, and **Intelligence** tabs in the UI.

---

## Verifying the change works

This stack is a multi-process system — there is **no single port** that
demonstrates correctness. To verify end-to-end after a code change:

1. `make up` — start everything.
2. `make logs` and confirm: `alembic upgrade head` succeeds, the API logs
   `app.startup`, and the worker logs `scheduler.start`.
3. `curl http://localhost:8000/health` → `{"status":"ok"}`.
4. `make seed` — ingests the sample transcript synchronously and prints the
   extracted JSON.
5. Open `http://localhost:5173` and check that the **Dashboard** shows
   non-zero counts and **Tasks** lists the extracted items.
6. Click **Intelligence → Run drift scan** to verify the monitor.
7. `make test` — runs the pytest suite inside the backend container against
   the live Postgres.

---

## Project layout

```
autonomous-ops-intelligence/
├── README.md                  ← you are here
├── docker-compose.yml
├── Makefile
├── .env.example
├── scripts/init-db.sql        ← creates pgvector + uuid-ossp + pg_trgm
├── examples/
│   ├── sample_transcript.txt
│   └── sample_transcript.json
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml         ← ruff + pytest config
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/0001_initial.py
│   ├── app/
│   │   ├── main.py            ← FastAPI app + routers
│   │   ├── config.py          ← typed env config
│   │   ├── database.py        ← async SQLAlchemy engine
│   │   ├── logging.py         ← structlog setup
│   │   ├── api/               ← HTTP routers
│   │   │   ├── transcripts.py
│   │   │   ├── projects.py
│   │   │   ├── tasks.py
│   │   │   ├── decisions.py
│   │   │   └── intelligence.py
│   │   ├── models/            ← SQLAlchemy ORM
│   │   ├── schemas/           ← Pydantic I/O models
│   │   ├── llm/
│   │   │   ├── client.py      ← OpenAI + Anthropic
│   │   │   ├── embeddings.py  ← OpenAI + local fallback
│   │   │   └── prompts.py     ← system + user prompts
│   │   ├── services/
│   │   │   ├── chunker.py
│   │   │   ├── extraction.py  ← the pipeline orchestrator
│   │   │   ├── project_resolver.py
│   │   │   └── search.py      ← pgvector cosine search
│   │   ├── integrations/
│   │   │   ├── base.py
│   │   │   ├── dispatcher.py
│   │   │   ├── notion.py
│   │   │   ├── linear.py
│   │   │   ├── jira.py
│   │   │   └── slack.py
│   │   └── workers/
│   │       ├── monitor.py     ← drift detection rules
│   │       └── scheduler.py   ← long-running loop
│   └── tests/
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── App.tsx
        ├── api.ts
        └── components/
            ├── Dashboard.tsx
            ├── TranscriptUpload.tsx
            ├── Tasks.tsx
            ├── Decisions.tsx
            └── Intelligence.tsx
```

---

## How extraction works

The LLM is called **once per transcript** with:

- the full system prompt (`backend/app/llm/prompts.py`) — strict rules about
  evidence, source quotes, confidence calibration, and JSON-only output;
- the transcript metadata (title, date, participants, optional project hint);
- **the project's existing state** — open tasks, recent decisions, and open
  blockers (see [Cross-meeting intelligence](#cross-meeting-intelligence));
- the transcript body;
- the JSON schema we expect back, derived from our pydantic models so the
  model never sees a schema that differs from what we validate against.

The response is then:

1. **Cleaned** — code fences and surrounding prose stripped.
2. **Parsed** — JSON → `ExtractionResult`.
3. **Validated** — pydantic enforces types, enum values, and confidence range.
4. **Reconciled** — status updates are applied to existing tasks and re-stated
   tasks are de-duplicated before anything new is written
   ([details below](#cross-meeting-intelligence)).
5. **Persisted** — every artefact carries `source_quote` and `confidence`, so a
   human reviewing the dashboard can audit why the LLM said what it said.

If any step fails, the transcript row is marked `failed` with the reason and
the pipeline rolls back — partial extractions never land.

---

## Cross-meeting intelligence

A naïve meeting agent is **stateless**: it reads one transcript, knows nothing
about the project's history, and so re-creates the same task at every standup,
re-reports a blocker that's still open, and never notices when someone says a
task is *done*. The result is a tracker that fills up with duplicates and drifts
out of sync with reality.

This agent has memory. Before extraction, it loads the project's current state
and feeds it to the model as context (`app/services/context.py`); after
extraction, it reconciles the model's output against that state
(`app/services/extraction.py`). Three things fall out of this:

| Behaviour                | What triggers it                                              | Effect                                                                                  |
| ------------------------ | ------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Status updates**       | The meeting reports progress on an existing task              | A `task_updates` entry closes/advances the task and logs a `status_change` activity — no duplicate row |
| **De-duplication**       | The model re-emits a task that already exists                 | A deterministic title-similarity guard (`app/services/dedup.py`) merges it into the existing task, gap-filling a missing owner/due date and logging `dedup_merged` |
| **Decision supersession**| A new decision reverses one already on record                | The prior decision is flagged `superseded` in its metadata                              |

The model is *asked* to emit updates instead of duplicates (it sees each
existing item tagged with its id), but the de-dup guard is a deterministic
safety net that runs regardless — it needs no embeddings or API key, so it works
even in the offline/local-fallback configuration.

**When it activates.** Continuity keys off the transcript's project. Because
`project_hint` resolves to (or creates) a project *before* extraction runs,
re-using the same hint across meetings is all it takes — the first meeting of a
project has no prior state, every subsequent one does. Set
`CROSS_MEETING_CONTEXT=false` to fall back to the original stateless behaviour.

Tuning knobs (see `.env.example`): `CONTEXT_MAX_TASKS` / `CONTEXT_MAX_DECISIONS`
/ `CONTEXT_MAX_BLOCKERS` bound how much history is injected (and thus token
cost); `DEDUP_TITLE_THRESHOLD` (0–1, default `0.82`) sets how aggressive the
merge is — higher is more conservative.

---

## Live note-taker (browser extension)

The batch flow (upload a transcript / video after the fact) is complemented by a
**Chrome extension that takes notes while the meeting is happening**. It captures
the active tab's audio, streams it to the backend for real-time transcription,
and shows tasks/decisions/risks/blockers in a side panel **as they're spoken**.

```
 Meeting tab audio ─► extension (offscreen AudioWorklet → 16 kHz PCM16)
                       │ WebSocket  /api/live/ws/{transcript_id}   (audio up)
                       ▼
                  backend relay ─► streaming STT (Deepgram) ─► transcript
                       │ debounced incremental extraction (reconciled)
                       ▼
                  JSON notes snapshot  ──────────────► side panel (live)
```

The clever part is that **live = many tiny meetings reconciled**. Every few
seconds the backend re-extracts the accumulated transcript and runs it through
the same [cross-meeting](#cross-meeting-intelligence) dedup/update engine, so a
task mentioned twice stays one row and "that's done" closes it — no duplicate
spam as the conversation repeats itself. At *finalize*, one full pass builds
embeddings and fires the Notion/Linear/Slack integrations exactly once.

Audio is relayed **through the backend**, so the STT key (`DEEPGRAM_API_KEY`)
stays a server-side `SecretStr` and never reaches the browser — same secret
discipline as everywhere else.

- Backend: `app/api/live.py` (WebSocket + session), `app/services/live_stt.py`
  (provider-agnostic streaming STT), `app/services/live_session.py` (extraction
  cadence), `ExtractionPipeline.extract_incremental` (cheap in-loop reconcile).
- Extension: `extension/` — load unpacked; see `extension/README.md` for setup.
- Config: `STT_PROVIDER=deepgram` (+`DEEPGRAM_API_KEY`) or `STT_PROVIDER=openai`
  (reuses `OPENAI_API_KEY`, Realtime transcription) enable transcription;
  `STT_PROVIDER=none` (default) leaves the socket working but silent. The backend
  reports the provider's expected capture sample rate to the extension.
- Resilience: the extension auto-reconnects with backoff if the socket drops, and
  the backend re-seeds the session from the persisted transcript, so a blip
  mid-meeting doesn't lose notes. A live indicator shows in the React console
  while a meeting is being transcribed.

---

## How drift detection works

`DriftMonitor.scan()` runs four cheap queries against the database:

| Rule              | Trigger                                                                    |
| ----------------- | -------------------------------------------------------------------------- |
| **overdue**       | active task whose `due_date < now() - OVERDUE_GRACE_DAYS`                  |
| **stalled**       | active task whose `last_status_change_at < now() - STALL_DAYS`             |
| **missing owner** | high/urgent task with no owner                                             |
| **aged blocker**  | open blocker older than 3 days                                             |

Findings are written to `task_activities` as `drift_*` entries (audit trail).
When `notify=True` and Slack is configured, the monitor messages once per
task per 24h (cooldown tracked on the task row itself).

The worker container loops this every `MONITOR_INTERVAL_SECONDS`. You can also
trigger an on-demand run from the **Intelligence** tab in the UI or via
`POST /api/intelligence/drift/run`.

---

## Secrets handling

Your `ANTHROPIC_API_KEY` (and any other credentials you set) are protected by
several layers — what each one does, in order from "happens automatically" to
"you opt in":

| Layer                                  | Status                  | What it protects against                                              |
| -------------------------------------- | ----------------------- | --------------------------------------------------------------------- |
| `.env` is in `.gitignore`              | ✅ Automatic             | Accidentally committing it to source control                          |
| `.env.example` ships empty             | ✅ Automatic             | The template can't leak real keys                                     |
| Key never baked into Docker image      | ✅ Automatic             | `docker save` / pushed images don't carry credentials                 |
| Pydantic `SecretStr` on every key      | ✅ Automatic             | Logs, tracebacks, repr, settings dumps render `'**********'` instead of the real key |
| `.env` created at mode `0600`          | ✅ `make up` does this    | Other users on the same machine can't read your `.env`                |
| `make secure-env`                      | ✅ Re-tighten on demand   | If `.env` was created by another tool with permissive perms           |
| macOS Keychain (optional)              | ⚪ See below              | Removes the file from disk entirely                                   |
| 1Password / Vault (optional)           | ⚪ Out of scope           | Centralised secret management for a team                              |

### Verify your `.env` is locked down

```bash
ls -l .env
# expected: -rw-------  1 yourname  staff  ...  .env
```

If the permissions are anything other than `600`:

```bash
make secure-env
```

### Optional: pull the key from macOS Keychain at runtime

If you'd rather not keep the key in a plaintext file at all, you can stash it
in the macOS Keychain and inject it into `docker compose` at start:

```bash
# Save the key once
security add-generic-password -a "$USER" -s "anthropic-aoi" -w "sk-ant-..."

# Then, instead of `make up`:
ANTHROPIC_API_KEY=$(security find-generic-password -a "$USER" -s "anthropic-aoi" -w) \
  docker compose up -d --build
```

The key is only resident in memory for that shell command; nothing hits disk.

### What `SecretStr` actually buys you

Without it, this kind of mistake would print your real key:

```python
logger.info("startup", settings=get_settings().model_dump())
```

With it, the same call prints:

```
settings={'anthropic_api_key': SecretStr('**********'), ...}
```

Every adapter unwraps the key via `.get_secret_value()` only at the point of
use, so the raw string lives in memory for the duration of a single HTTP call
and nowhere else.

---

## Configuration reference

See `.env.example` for the canonical list. Highlights:

- `LLM_PROVIDER` — `openai` or `anthropic`
- `EMBEDDING_PROVIDER` — `openai` or `local` (deterministic offline fallback)
- `MONITOR_INTERVAL_SECONDS` — drift scan cadence
- `STALL_DAYS` — stall threshold
- `CROSS_MEETING_CONTEXT` — feed prior project state into extraction (default `true`)
- `DEDUP_TITLE_THRESHOLD` — task de-dup aggressiveness, 0–1 (default `0.82`)
- `STT_PROVIDER` — `deepgram`, `openai` (Realtime), or `none`; enables the live note-taker
- `DEEPGRAM_API_KEY` — streaming STT key for live transcription (server-side only)
- `INGEST_API_KEY` — set to require `X-API-Key` on transcript ingestion

---

## Tests

```bash
make test
```

This runs `pytest -v` inside the backend container so the Postgres container
is reachable. Tests split into:

- **Pure unit** — chunker, prompt parser, slug helper, local embedder, and the
  task de-dup matcher (`tests/test_dedup.py`). Always run.
- **Integration** — extraction pipeline (incl. cross-meeting status updates,
  de-duplication, and decision supersession), drift monitor. Auto-skip when
  Postgres+pgvector isn't reachable (handy for CI quick checks).

---

## Mapping to the original phases

| Phase                          | Implementation                                                            |
| ------------------------------ | ------------------------------------------------------------------------- |
| 1. Core pipeline               | `app/services/extraction.py` + `app/llm/*` + `app/services/chunker.py`    |
| 2. Operational tools           | `app/integrations/{notion,linear,jira,slack}.py` + `dispatcher.py`        |
| 3. Workflow automation         | `app/workers/scheduler.py` (asyncio loop with retries via `tenacity`). The full Temporal/Kafka rewrite is left as a follow-up — the abstractions in `dispatcher.py` and `monitor.py` already cleanly support being lifted into Temporal workflows. |
| 4. Operational intelligence    | `app/workers/monitor.py` — drift detection, escalation, reminder cooldown |
| 5. Deployment                  | `docker-compose.yml` brings up a complete stack you can hand to a team    |

---

## Where to extend next

- **Replace the scheduler with Temporal.io.** The `DriftMonitor` and
  `IntegrationDispatcher` are already side-effecty-but-pure-input — they will
  port directly to activities and workflows.
- **Add contradiction detection.** Embed every decision and flag pairs whose
  cosine similarity is high but whose source quotes are semantically opposed.
- **Two-way sync.** Right now we push to Linear/Notion; pulling status changes
  back into the task model would close the loop.
- **Webhooks.** Replace polling with Linear/Notion/Slack webhooks for
  near-real-time updates.
- **Auth.** The ingest API supports a static key; swap that for OIDC if you
  deploy this for a real org.

---

## License

This is your codebase — use it however you want.
