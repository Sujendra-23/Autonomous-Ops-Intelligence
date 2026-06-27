"""Decisions browsing endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.decision import Decision

router = APIRouter()


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    transcript_id: uuid.UUID | None
    summary: str
    rationale: str | None
    decided_by: list[str] | None
    source_quote: str | None
    confidence: float | None
    created_at: datetime


class DecisionList(BaseModel):
    items: list[DecisionOut]
    total: int


@router.get("", response_model=DecisionList)
async def list_decisions(
    project_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> DecisionList:
    stmt = select(Decision).order_by(Decision.created_at.desc())
    if project_id:
        stmt = stmt.where(Decision.project_id == project_id)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return DecisionList(items=[DecisionOut.model_validate(r) for r in rows], total=int(total or 0))
