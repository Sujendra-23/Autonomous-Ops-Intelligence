"""Authenticated NL analytics. Permission derives only from server-held credentials."""

from __future__ import annotations

import secrets
from typing import Any

import anthropic
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.services.analytics import AnalyticsUnavailable, UnsafeQuery, execute_sql, generate_sql

router = APIRouter()


async def analytics_permission(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    settings = get_settings()
    if settings.auth_mode == "oidc":
        raise HTTPException(503, "Natural-language SQL analytics is unavailable in SaaS mode")
    normal = settings.intelligence_api_key.get_secret_value()
    owner = settings.intelligence_owner_api_key.get_secret_value()
    if not normal or (owner and secrets.compare_digest(normal, owner)):
        raise HTTPException(503, "Analytics authentication is not configured")
    if x_api_key and owner and secrets.compare_digest(x_api_key, owner):
        return True  # intelligence:read_owners
    if x_api_key and secrets.compare_digest(x_api_key, normal):
        return False
    raise HTTPException(401, "Invalid or missing analytics X-API-Key")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class AskResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    owners_masked: bool


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest, read_owners: bool = Depends(analytics_permission)) -> dict:
    try:
        sql = await generate_sql(payload.question, read_owners=read_owners)
        return await execute_sql(sql, read_owners=read_owners)
    except UnsafeQuery:
        raise HTTPException(
            422, "Question could not be answered with a safe semantic query"
        ) from None
    except AnalyticsUnavailable:
        raise HTTPException(503, "Analytics is not configured or unavailable") from None
    except anthropic.AnthropicError:
        raise HTTPException(502, "Analytics model is unavailable") from None
    except (SQLAlchemyError, TimeoutError, OSError):
        # Never return/log SQL, prompts, credentials, or database error details.
        raise HTTPException(503, "Analytics query could not be completed") from None
