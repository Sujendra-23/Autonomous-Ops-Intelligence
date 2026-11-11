"""Task listing, status updates, and activity history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ingest_key
from app.database import get_session
from app.integrations.task_import import import_connected_tasks, import_status
from app.integrations.task_sync import mark_sync_pending
from app.integrations.webhooks import enqueue_event, task_data
from app.models.task import Task, TaskActivity, TaskStatus

router = APIRouter()


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    transcript_id: uuid.UUID | None
    title: str
    description: str | None
    source: str = "meeting"
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
    sync_pending: bool
    sync_checked_at: datetime | None
    sync_error: str | None

    @field_validator("source", mode="before")
    @classmethod
    def source_default(cls, value):
        return value or "meeting"


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512, pattern=r"\S")
    description: str | None = Field(default=None, max_length=100000)
    owner: str | None = Field(default=None, max_length=128)
    due_date: datetime | None = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        return value.strip()


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512, pattern=r"\S")
    description: str | None = Field(default=None, max_length=100000)
    status: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None
    owner: str | None = Field(default=None, max_length=128)
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
    source: Literal["local", "meeting", "jira", "linear"] | None = None,
    q: str | None = Query(None, max_length=200),
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
    if source:
        stmt = stmt.where(Task.source == source)
    if q and q.strip():
        term = q.strip()
        stmt = stmt.where(
            or_(
                Task.title.icontains(term, autoescape=True),
                Task.owner.icontains(term, autoescape=True),
            )
        )
    if overdue:
        stmt = stmt.where(
            Task.due_date.is_not(None),
            Task.due_date < func.now(),
            Task.status.in_(("open", "in_progress", "blocked")),
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return TaskList(items=[TaskOut.model_validate(r) for r in rows], total=int(total or 0))


@router.post(
    "", response_model=TaskOut, status_code=201, dependencies=[Depends(require_ingest_key)]
)
async def create_task(payload: TaskCreate, session: AsyncSession = Depends(get_session)):
    task = Task(
        id=uuid.uuid4(),
        source="local",
        status="open",
        sync_pending=False,
        last_status_change_at=datetime.now(UTC),
        **payload.model_dump(),
    )
    session.add(task)
    await session.flush()
    session.add(
        TaskActivity(task_id=task.id, kind="created", actor="user", payload={"source": "local"})
    )
    enqueue_event(session, "task.created", task_data(task))
    await session.commit()
    await session.refresh(task)
    return TaskOut.model_validate(task)


# These fixed paths must precede /{task_id}.
@router.get("/sync")
async def task_sync_status(session: AsyncSession = Depends(get_session)):
    return await import_status(session)


@router.post("/sync", dependencies=[Depends(require_ingest_key)])
async def sync_tasks(session: AsyncSession = Depends(get_session)):
    results = await import_connected_tasks(session, force=True)
    return {"results": results, **await import_status(session)}


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> TaskOut:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskOut.model_validate(task)


@router.patch("/{task_id}", response_model=TaskOut, dependencies=[Depends(require_ingest_key)])
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
) -> TaskOut:
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")

    if (task.source in {"jira", "linear"} or task.jira_issue_url or task.linear_issue_url) and (
        payload.model_fields_set & {"title", "description", "owner", "due_date", "priority"}
    ):
        raise HTTPException(
            409, "Edit linked task details in the original tool; AOI imports those fields."
        )

    changed: dict[str, object] = {}
    if payload.title is not None and payload.title.strip() != task.title:
        changed["title"] = {"from": task.title, "to": payload.title.strip()}
        task.title = payload.title.strip()
    if "description" in payload.model_fields_set and payload.description != task.description:
        changed["description"] = {"from": task.description, "to": payload.description}
        task.description = payload.description
    if payload.status and payload.status != task.status:
        changed["status"] = {"from": task.status, "to": payload.status}
        task.status = payload.status
        task.last_status_change_at = datetime.now(UTC)
        mark_sync_pending(task)
    if "owner" in payload.model_fields_set and payload.owner != task.owner:
        changed["owner"] = {"from": task.owner, "to": payload.owner}
        task.owner = payload.owner or None
    if "due_date" in payload.model_fields_set and payload.due_date != task.due_date:
        changed["due_date"] = {
            "from": task.due_date.isoformat() if task.due_date else None,
            "to": payload.due_date.isoformat() if payload.due_date else None,
        }
        task.due_date = payload.due_date
    if payload.priority and payload.priority != task.priority:
        changed["priority"] = {"from": task.priority, "to": payload.priority}
        task.priority = payload.priority

    if changed:
        enqueue_event(session, "task.updated", {**task_data(task), "changes": changed})
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
        (
            await session.execute(
                select(TaskActivity)
                .where(TaskActivity.task_id == task_id)
                .order_by(TaskActivity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [TaskActivityOut.model_validate(r) for r in rows]


# Keep the enum reachable so OpenAPI surfaces valid statuses.
_ = TaskStatus
