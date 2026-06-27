"""Project CRUD + per-project rollups."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task

router = APIRouter()


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: str
    notion_page_id: str | None
    linear_project_id: str | None


class ProjectRollup(ProjectOut):
    open_tasks: int
    overdue_tasks: int
    open_blockers: int
    open_risks: int
    decisions_count: int


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    rows = (
        await session.execute(select(Project).order_by(Project.name))
    ).scalars().all()
    return [ProjectOut.model_validate(r) for r in rows]


@router.get("/{project_id}", response_model=ProjectRollup)
async def get_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ProjectRollup:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    open_tasks = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.project_id == project_id, Task.status != "done", Task.status != "cancelled")
    )
    overdue_tasks = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.project_id == project_id,
            Task.status.in_(("open", "in_progress", "blocked")),
            Task.due_date.is_not(None),
            Task.due_date < func.now(),
        )
    )
    open_blockers = await session.scalar(
        select(func.count())
        .select_from(Blocker)
        .where(Blocker.project_id == project_id, Blocker.status == "open")
    )
    open_risks = await session.scalar(
        select(func.count())
        .select_from(Risk)
        .where(Risk.project_id == project_id, Risk.status == "open")
    )
    decisions_count = await session.scalar(
        select(func.count()).select_from(Decision).where(Decision.project_id == project_id)
    )

    return ProjectRollup(
        **ProjectOut.model_validate(project).model_dump(),
        open_tasks=int(open_tasks or 0),
        overdue_tasks=int(overdue_tasks or 0),
        open_blockers=int(open_blockers or 0),
        open_risks=int(open_risks or 0),
        decisions_count=int(decisions_count or 0),
    )
