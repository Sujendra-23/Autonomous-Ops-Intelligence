"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse

from app import __version__
from app.api import (
    account,
    decisions,
    integrations,
    intelligence,
    live,
    projects,
    tasks,
    transcripts,
)
from app.config import get_settings
from app.logging import configure_logging, get_logger
from app.security import SecurityMiddleware

configure_logging()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auth_mode == "oidc":
        from sqlalchemy import text

        from app.database import engine

        async with engine.connect() as connection:
            privileged = await connection.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
            if privileged:
                raise RuntimeError(
                    "SaaS requires a database role without superuser or BYPASSRLS privileges"
                )
            policies = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace "
                    "WHERE n.nspname='public' AND relname IN "
                    "('projects','transcripts','transcript_chunks','tasks','task_activities',"
                    "'decisions','risks','blockers','webhook_deliveries') "
                    "AND relrowsecurity AND relforcerowsecurity"
                )
            )
            if policies != 9:
                raise RuntimeError(
                    "Workspace isolation migration must be applied before serving traffic"
                )
    logger.info(
        "app.startup",
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        notion=settings.notion_enabled,
        linear=settings.linear_enabled,
        jira=settings.jira_enabled,
        slack=settings.slack_enabled,
    )
    yield
    logger.info("app.shutdown")


app = FastAPI(
    title="Autonomous Operational Intelligence Layer",
    version=__version__,
    summary="Structured tasks and decisions from meetings, with zero manual tagging.",
    description=(
        "An autonomous AI agent that converts meeting transcripts into "
        "**structured tasks, decisions, risks, and blockers** — each with an "
        "owner, due date, priority, verbatim source quote, and confidence "
        "score — and mirrors them into Notion, Linear/Jira, and Slack "
        "automatically.\n\n"
        "**Zero manual tagging.** **Security is a first-class product concern:** "
        "every credential is wrapped in `pydantic.SecretStr` so it can never "
        "leak into logs, tracebacks, or settings dumps, and the `.env` file is "
        "auto-created at mode `0600`.\n\n"
        "After each meeting the agent doesn't go to sleep — a long-running "
        "drift monitor catches overdue, stalled, unowned, and aged-blocker "
        "work and nudges Slack with per-task reminder cooldowns."
    ),
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/docs" if get_settings().environment == "development" else None,
    redoc_url="/redoc" if get_settings().environment == "development" else None,
    openapi_url="/openapi.json" if get_settings().environment == "development" else None,
)

app.add_middleware(SecurityMiddleware)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", path=request.url.path, error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error"},
    )


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "name": "Autonomous Operational Intelligence Layer",
        "version": __version__,
        "docs": "/docs",
    }


app.include_router(transcripts.router, prefix="/api/transcripts", tags=["transcripts"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(decisions.router, prefix="/api/decisions", tags=["decisions"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["intelligence"])
app.include_router(live.router, prefix="/api/live", tags=["live"])
app.include_router(integrations.router, prefix="/api/integrations", tags=["integrations"])

app.include_router(account.router, prefix="/api/account", tags=["account"])


@app.get("/ready", tags=["meta"])
async def ready():
    from redis.asyncio import Redis
    from sqlalchemy import text

    from app.database import engine

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        async with Redis.from_url(get_settings().redis_url) as redis:
            await redis.ping()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ready"}
