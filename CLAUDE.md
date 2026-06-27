# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make up          # build + start all five containers (postgres, redis, backend, worker, frontend)
make down        # stop the stack
make build       # rebuild only the backend image (required after Dockerfile changes)
make logs        # tail all container logs
make migrate     # run alembic upgrade head inside the running backend container
make test        # pytest -v inside the backend container (Postgres reachable)
make fmt         # ruff format app tests
make lint        # ruff check app tests
make shell       # /bin/sh inside the backend container
make seed        # POST examples/sample_transcript.json to the live API
make clean       # docker compose down -v  ← destroys volumes
```

Run a single test file:
```bash
docker compose exec backend pytest tests/test_chunker.py -v
```

Create a new migration after changing a model:
```bash
docker compose exec backend alembic revision --autogenerate -m "short description"
```

The backend mounts `./backend:/app` and runs uvicorn with `--reload`, so Python changes are picked up without a restart. Dockerfile changes (e.g. adding system packages) require `make build && make up`.

## Stack at a glance

| Service | Image | Port |
|---|---|---|
| postgres | pgvector/pgvector:pg16 | 5432 |
| redis | redis:7-alpine | 6379 |
| backend | ./backend/Dockerfile | 8000 |
| worker | same image, different entrypoint | — |
| frontend | node:20-alpine | 5173 |

API docs: `http://localhost:8000/docs`. `.env` is auto-created at mode `0600` by `make up`; all credentials must be `pydantic.SecretStr` and unwrapped via `.get_secret_value()` only at the point of use.

## Architecture

### Request → extraction flow

```
POST /api/transcripts          (JSON body, text transcript)
POST /api/transcripts/upload   (multipart, video/audio file)
```

Both paths end at `ExtractionPipeline.process()` in `backend/app/services/extraction.py`. The pipeline is synchronous-within-async and runs in the same request for the default `sync_extract=True` case. Status is written to the transcript row at each stage so partial failures leave breadcrumbs:

```
chunking → extracting → completed
                      ↘ failed  (rolls back artefacts, sets transcript.error)
```

**LLM is called once per transcript with the full text** — chunking is for embedding/search only, not for the extraction call.

### Video upload path

`POST /api/transcripts/upload` streams the file to a temp directory (never loads into RAM), then:
1. `ffmpeg` re-encodes to 32 kbps mono mp3 (1 h ≈ 14 MB)
2. If compressed file > 20 MB, split into 80-min segments
3. Each segment → OpenAI `whisper-1` → text
4. Joined text → `ExtractionPipeline`

`OPENAI_API_KEY` is required for Whisper even when `LLM_PROVIDER=anthropic`.

### Integration dispatch

After extraction, `IntegrationDispatcher.publish()` fans out to adapters. Each adapter calls `is_enabled()` and short-circuits when its keys are not configured — adding integrations to an existing deployment is a pure `.env` change. Key constraint: **Linear takes precedence over Jira** for task mirroring; only the first enabled task mirror fires per task.

### Drift monitor worker

`backend/app/workers/scheduler.py` is the `worker` container entrypoint. It runs `DriftMonitor.scan(notify=True)` every `MONITOR_INTERVAL_SECONDS` in an asyncio loop with `SIGINT`/`SIGTERM` handling. No Celery or Temporal — just a plain asyncio loop against the shared Postgres. An on-demand scan is also exposed at `POST /api/intelligence/drift/run`.

### LLM / embedding providers

Configured via `LLM_PROVIDER` (`anthropic` | `openai`). Clients are singletons via `@lru_cache` in `app/llm/client.py`. Embeddings are separate (`EMBEDDING_PROVIDER=openai|local`); the `local` fallback is deterministic but not semantic — fine for dev without an OpenAI key.

## Key conventions

**Route ordering matters.** FastAPI matches routes in registration order. Literal path segments (e.g. `/upload`) must be registered before parameterized routes (`/{transcript_id}`) that share the same HTTP method. The `/upload` route is intentionally first in `app/api/transcripts.py`.

**Secrets.** All API keys in `app/config.py` are `pydantic.SecretStr`. Never add a plain `str` config field for a credential.

**Ruff config** (`pyproject.toml`): line-length 100, Python 3.11, rulesets E/F/W/I/B/UP/S/A/RUF. `S101` (assert) and `B008` (FastAPI `Depends`) are suppressed globally. Alembic versions folder is excluded.

**Tests.** `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed. Integration tests that need Postgres auto-skip when the DB is unreachable; pure unit tests always run.
