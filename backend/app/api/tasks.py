"""Task listing, status updates, and activity history."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.task import Task, TaskActivity, TaskStatus

router = APIRouter()


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    transcript_id: uuid.UUID | None
    title: str
    description: str | None
    owner: str | None
    due_date: datetime | None
    status: str
    priority: str
    source_quote: str | None
    confidence: float | None
    linear_issue_url: str | None
    jira_issue_url: str | None
    created_at: datetime
    last_status_change_at: datetime


class TaskUpdate(BaseModel):
    status: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None
    owner: str | None = None
    due_date: datetime | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None


class TaskActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    kind: str
    payload: dict | None
    actor: str
    created_at: datetime


class TaskList(BaseModel):
    items: list[TaskOut]
    total: int


@router.get("", response_model=TaskList)
async def list_tasks(
    status: str | None = None,
    project_id: uuid.UUID | None = None,
    owner: str | None = None,
    overdue: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> TaskList:
    stmt = select(Task).order_by(Task.due_date.is_(None), Task.due_date, Task.created_at.desc())
    if status:
        stmt = stmt.where(Task.status == status)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if owner:
        stmt = stmt.where(Task.owner == owner)
    if overdue:
        stmt = stmt.where(
            Task.due_date.is_not(None),
            Task.due_date < func.now(),
            Task.status.in_(("open", "in_progress", "blocked")),
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return TaskList(items=[TaskOut.model_validate(r) for r in rows], total=int(total or 0))


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> TaskOut:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskOut.model_validate(task)


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
) -> TaskOut:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")

    changed: dict[str, object] = {}
    if payload.status and payload.status != task.status:
        changed["status"] = {"from": task.status, "to": payload.status}
        task.status = payload.status
        task.last_status_change_at = datetime.utcnow()
    if payload.owner is not None and payload.owner != task.owner:
        changed["owner"] = {"from": task.owner, "to": payload.owner}
        task.owner = payload.owner or None
    if payload.due_date is not None and payload.due_date != task.due_date:
        changed["due_date"] = {
            "from": task.due_date.isoformat() if task.due_date else None,
            "to": payload.due_date.isoformat(),
        }
        task.due_date = payload.due_date
    if payload.priority and payload.priority != task.priority:
        changed["priority"] = {"from": task.priority, "to": payload.priority}
        task.priority = payload.priority

    if changed:
        session.add(
            TaskActivity(
                task_id=task.id,
                kind="updated",
                payload=changed,
                actor="user",
            )
        )
    await session.commit()
    await session.refresh(task)
    return TaskOut.model_validate(task)


@router.get("/{task_id}/activity", response_model=list[TaskActivityOut])
async def task_activity(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[TaskActivityOut]:
    rows = (
        await session.execute(
            select(TaskActivity)
            .where(TaskActivity.task_id == task_id)
            .order_by(TaskActivity.created_at.desc())
        )
    ).scalars().all()
    return [TaskActivityOut.model_validate(r) for r in rows]


# Keep the enum reachable so OpenAPI surfaces valid statuses.
_ = TaskStatus
