"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys

import structlog

from app.config import get_settings


def scrub_private_fields(logger, method, event):
    for key in list(event):
        if key not in {
            "event",
            "level",
            "timestamp",
            "environment",
            "llm_provider",
            "operation",
            "status",
            "error_type",
        } and any(
            part in key.lower()
            for part in (
                "secret",
                "token",
                "password",
                "credential",
                "title",
                "content",
                "text",
                "prompt",
                "quote",
                "filename",
                "error",
                "detail",
                "exc_info",
                "stack",
            )
        ):
            event[key] = "[redacted]"
    return event


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if settings.environment == "production":
        processors.append(scrub_private_fields)
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
