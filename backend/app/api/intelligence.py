"""Operational intelligence — dashboard data + semantic search + drift runs."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.search import semantic_search
from app.workers.monitor import DriftMonitor

router = APIRouter()


class DashboardCounts(BaseModel):
    transcripts: int
    projects: int
    open_tasks: int
    overdue_tasks: int
    stalled_tasks: int
    open_blockers: int
    open_risks: int
    decisions: int


class DriftItem(BaseModel):
    kind: Literal["overdue", "stalled", "missing_owner", "unresolved_blocker"]
    severity: Literal["info", "warning", "critical"]
    task_id: uuid.UUID | None
    blocker_id: uuid.UUID | None
    project_id: uuid.UUID | None
    title: str
    detail: str


class DriftReport(BaseModel):
    generated_at: datetime
    items: list[DriftItem]


class SearchHitOut(BaseModel):
    chunk_id: str
    transcript_id: str
    transcript_title: str
    content: str
    score: float


@router.get("/dashboard", response_model=DashboardCounts)
async def dashboard(session: AsyncSession = Depends(get_session)) -> DashboardCounts:
    s = get_settings()
    stall_cutoff = datetime.utcnow() - timedelta(days=s.stall_days)

    transcripts = await session.scalar(select(func.count()).select_from(Transcript))
    projects = await session.scalar(select(func.count()).select_from(Project))
    open_tasks = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.status.in_(("open", "in_progress", "blocked")))
    )
    overdue_tasks = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.status.in_(("open", "in_progress", "blocked")),
            Task.due_date.is_not(None),
            Task.due_date < func.now(),
        )
    )
    stalled_tasks = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.status.in_(("open", "in_progress")),
            Task.last_status_change_at < stall_cutoff,
        )
    )
    open_blockers = await session.scalar(
        select(func.count()).select_from(Blocker).where(Blocker.status == "open")
    )
    open_risks = await session.scalar(
        select(func.count()).select_from(Risk).where(Risk.status == "open")
    )
    decisions = await session.scalar(select(func.count()).select_from(Decision))

    return DashboardCounts(
        transcripts=int(transcripts or 0),
        projects=int(projects or 0),
        open_tasks=int(open_tasks or 0),
        overdue_tasks=int(overdue_tasks or 0),
        stalled_tasks=int(stalled_tasks or 0),
        open_blockers=int(open_blockers or 0),
        open_risks=int(open_risks or 0),
        decisions=int(decisions or 0),
    )


@router.post("/drift/run", response_model=DriftReport)
async def run_drift(
    notify: bool = Query(False, description="Also fire Slack reminders."),
    session: AsyncSession = Depends(get_session),
) -> DriftReport:
    monitor = DriftMonitor(session)
    items = await monitor.scan(notify=notify)
    return DriftReport(
        generated_at=datetime.utcnow(),
        items=[DriftItem(**i) for i in items],
    )


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


@router.post("/search", response_model=list[SearchHitOut])
async def search(
    payload: SearchRequest = Body(...),
    session: AsyncSession = Depends(get_session),
) -> list[SearchHitOut]:
    hits = await semantic_search(session, payload.query, limit=payload.limit)
    return [SearchHitOut(**hit.__dict__) for hit in hits]
