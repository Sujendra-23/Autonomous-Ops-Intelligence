"""Project roadmap generation and versioned saves within existing tenant boundaries."""

import hashlib
import uuid
from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ingest_key
from app.database import get_session
from app.llm.client import LLMError
from app.models.project import Project
from app.models.task import Task, TaskActivity
from app.models.transcript import Transcript
from app.schemas.roadmap import ProjectRoadmap, RoadmapDocument, RoadmapGenerate, RoadmapSave
from app.services.dlp import redact_payload
from app.services.project_resolver import slugify
from app.services.roadmap import generate_roadmap

router = APIRouter()


def project_roadmap(project: Project) -> ProjectRoadmap:
    return ProjectRoadmap(
        project_id=project.id,
        name=project.name,
        version=project.roadmap_version or 0,
        roadmap=project.roadmap,
    )


@router.post(
    "/roadmap/generate", response_model=RoadmapDocument, dependencies=[Depends(require_ingest_key)]
)
async def generate(payload: RoadmapGenerate, session: AsyncSession = Depends(get_session)):
    try:
        return await generate_roadmap(session, payload)
    except LLMError as exc:
        # Provider responses and configuration values never reach the browser.
        raise HTTPException(
            503,
            "Roadmap generation could not finish. Check your workspace's "
            "AI configuration, then try again. Your plan has not been saved.",
        ) from exc


@router.get("/{project_id}/roadmap", response_model=ProjectRoadmap)
async def get_roadmap(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project_roadmap(project)


@router.post("/roadmap", response_model=ProjectRoadmap, dependencies=[Depends(require_ingest_key)])
async def save(payload: RoadmapSave, session: AsyncSession = Depends(get_session)):
    try:
        return await persist_roadmap(payload, session)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409,
            "A project with this name already exists. Choose it above " "or use a different name.",
        ) from exc


async def persist_roadmap(payload: RoadmapSave, session: AsyncSession):
    if payload.project_id:
        project = await session.get(Project, payload.project_id, with_for_update=True)
        if project is None:
            raise HTTPException(404, "Project not found")
        if (project.roadmap_version or 0) != payload.expected_version:
            raise HTTPException(
                409, "This roadmap changed. Reload the saved roadmap before editing."
            )
    else:
        if payload.expected_version != 0:
            raise HTTPException(422, "A new project must start at version zero")
        project = Project(id=uuid.uuid4(), name=payload.name.strip(), slug=slugify(payload.name))
        session.add(project)
    for meeting_id in payload.roadmap.source_meeting_ids:
        meeting = await session.get(Transcript, meeting_id)
        if meeting is None:
            raise HTTPException(404, "A source meeting is no longer available")
        if payload.project_id and meeting.project_id != payload.project_id:
            raise HTTPException(422, "Source meetings must belong to this project")
    document = RoadmapDocument.model_validate(
        redact_payload(payload.roadmap.model_dump(mode="json"))
    )
    created = 0
    if payload.create_tasks:
        await session.flush()
        for milestone in document.milestones:
            links = []
            for index, title in enumerate(milestone.deliverables):
                key = hashlib.sha256(f"{milestone.title}:{title}".casefold().encode()).hexdigest()
                task = None
                if index < len(milestone.task_ids):
                    task = await session.get(Task, milestone.task_ids[index])
                    if task is None or task.project_id != project.id or task.source != "local":
                        raise HTTPException(422, "A linked task is not available in this project")
                if task is None:
                    task = await session.scalar(
                        select(Task).where(
                            Task.project_id == project.id,
                            Task.source == "local",
                            Task.extra_metadata["roadmap_key"].astext == key,
                        )
                    )
                if task is None:
                    due = (
                        datetime.combine(document.start_date, time(17), tzinfo=UTC)
                        + timedelta(days=milestone.end_week * 7 - 1)
                        if document.start_date
                        else None
                    )
                    task = Task(
                        id=uuid.uuid4(),
                        project_id=project.id,
                        title=title,
                        description=milestone.objective,
                        source="local",
                        status="open",
                        priority="medium",
                        owner=milestone.owner,
                        due_date=due,
                        sync_pending=False,
                        last_status_change_at=datetime.now(UTC),
                        extra_metadata={"roadmap_key": key, "milestone": milestone.title},
                    )
                    session.add(task)
                    await session.flush()
                    session.add(
                        TaskActivity(
                            task_id=task.id,
                            kind="created",
                            actor="user",
                            payload={"source": "roadmap"},
                        )
                    )
                    created += 1
                links.append(task.id)
            milestone.task_ids = links
    project.roadmap = document.model_dump(mode="json")
    project.roadmap_version = (project.roadmap_version or 0) + 1
    await session.commit()
    return project_roadmap(project).model_copy(update={"created_task_count": created})
