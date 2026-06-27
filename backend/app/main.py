"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse

from app import __version__
from app.api import decisions, intelligence, live, projects, tasks, transcripts
from app.config import get_settings
from app.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
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
    summary="An AI Agent for Work — structured tasks and decisions from meetings, with zero manual tagging.",
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
    docs_url="/docs",
    redoc_url="/redoc",
)

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
    logger.exception("unhandled_exception", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "type": exc.__class__.__name__},
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
