"""End-to-end extraction with a stubbed LLM and stubbed integrations.

Skipped when Postgres+pgvector isn't reachable (see `conftest.db_available`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.integrations.base import DispatchResult
from app.integrations.dispatcher import IntegrationDispatcher
from app.models.decision import Decision
from app.models.project import Project
from app.models.task import Task, TaskActivity
from app.models.transcript import Transcript
from app.schemas.extraction import (
    BlockerExtraction,
    DecisionExtraction,
    ExtractionResult,
    RiskExtraction,
    TaskExtraction,
    TaskUpdateExtraction,
)
from app.services.extraction import ExtractionPipeline


FAKE_RESULT = ExtractionResult(
    project_hint="API Migration",
    summary="The team reviewed the migration plan and assigned ownership.",
    tasks=[
        TaskExtraction(
            title="Finalize migration plan",
            owner="John",
            priority="high",
            source_quote="John will own the migration plan by Friday.",
            confidence=0.95,
        )
    ],
    decisions=[
        DecisionExtraction(
            summary="Adopt the staged migration approach",
            decided_by=["John", "Priya"],
            source_quote="We agreed to stage the migration in three phases.",
            confidence=0.9,
        )
    ],
    risks=[
        RiskExtraction(
            title="Possible data loss during phase 2",
            severity="high",
            likelihood="medium",
            source_quote="If we don't snapshot before phase 2 we might lose data.",
            confidence=0.8,
        )
    ],
    blockers=[
        BlockerExtraction(
            summary="Waiting on prod DB access",
            blocked_party="John",
            needs_from="Infra team",
            severity="medium",
            source_quote="John can't proceed until infra grants prod access.",
            confidence=0.85,
        )
    ],
)


class _NoopDispatcher(IntegrationDispatcher):
    def __init__(self) -> None:
        super().__init__(project_mirrors=[], task_mirrors=[], notifiers=[])

    async def publish(self, **kwargs) -> list[DispatchResult]:  # type: ignore[override]
        return []


@pytest.mark.asyncio
async def test_pipeline_persists_all_extraction_types(db_session) -> None:
    transcript = Transcript(
        title="Migration sync",
        content=(
            "John: I'll own the migration plan by Friday.\n\n"
            "Priya: Agreed. We'll stage in three phases.\n\n"
            "John: I'm blocked on prod DB access from infra.\n\n"
            "Priya: There's a real risk of data loss in phase 2."
        ),
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)

    with patch("app.services.extraction.get_llm_client") as mock_client:
        mock_client.return_value.extract = AsyncMock(return_value=FAKE_RESULT)
        pipeline = ExtractionPipeline(db_session, dispatcher=_NoopDispatcher())
        out = await pipeline.process(transcript.id)

    assert out is not None
    await db_session.refresh(transcript)
    assert transcript.status == "completed"
    assert transcript.processed_at is not None
    assert transcript.project_id is not None

    from sqlalchemy import select

    tasks = (
        await db_session.execute(
            select(Task).where(Task.transcript_id == transcript.id)
        )
    ).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].owner == "John"
    assert tasks[0].priority == "high"
    assert tasks[0].source_quote is not None


async def _seed_project_with_open_task(
    db_session,
    *,
    title: str,
    owner: str | None = None,
    status: str = "in_progress",
) -> tuple[Project, Task]:
    project = Project(name="API Migration", slug="api-migration")
    db_session.add(project)
    await db_session.flush()
    task = Task(project_id=project.id, title=title, owner=owner, status=status)
    db_session.add(task)
    await db_session.flush()
    return project, task


@pytest.mark.asyncio
async def test_pipeline_feeds_prior_context_and_applies_task_update(db_session) -> None:
    """A follow-up meeting closes an existing task instead of duplicating it."""
    project, existing = await _seed_project_with_open_task(
        db_session, title="Finalize migration plan", owner="John"
    )
    existing_id = existing.id

    transcript = Transcript(
        title="Migration sync — week 2",
        content="John: the migration plan is finished and merged.\n\n" * 3,
        project_id=project.id,
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)

    result = ExtractionResult(
        project_hint="API Migration",
        summary="John reported the migration plan is complete.",
        task_updates=[
            TaskUpdateExtraction(
                task_id=str(existing_id),
                new_status="done",
                note="migration plan finished",
                source_quote="the migration plan is finished and merged",
                confidence=0.95,
            )
        ],
    )

    with patch("app.services.extraction.get_llm_client") as mock_client:
        extract = AsyncMock(return_value=result)
        mock_client.return_value.extract = extract
        pipeline = ExtractionPipeline(db_session, dispatcher=_NoopDispatcher())
        await pipeline.process(transcript.id)

    # The extractor was handed the existing task as prior context.
    _, kwargs = extract.call_args
    assert kwargs["prior_context"] is not None
    assert str(existing_id) in kwargs["prior_context"]
    assert "Finalize migration plan" in kwargs["prior_context"]

    from sqlalchemy import select

    await db_session.refresh(existing)
    assert existing.status == "done"

    # No duplicate task was created for this transcript.
    new_tasks = (
        await db_session.execute(select(Task).where(Task.transcript_id == transcript.id))
    ).scalars().all()
    assert new_tasks == []

    activities = (
        await db_session.execute(
            select(TaskActivity).where(TaskActivity.task_id == existing_id)
        )
    ).scalars().all()
    assert any(a.kind == "status_change" for a in activities)


@pytest.mark.asyncio
async def test_pipeline_dedups_restated_task_and_enriches_owner(db_session) -> None:
    """A re-stated task merges into the existing row instead of duplicating."""
    project, existing = await _seed_project_with_open_task(
        db_session, title="Finalize the migration plan", owner=None, status="open"
    )
    existing_id = existing.id

    transcript = Transcript(
        title="Migration sync — week 2",
        content="We still need to finalize the migration plan; Priya will own it.\n\n" * 3,
        project_id=project.id,
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)

    # Model fails to use context and re-emits the task (near-identical title).
    result = ExtractionResult(
        project_hint="API Migration",
        summary="The migration plan still needs finalizing; Priya will own it.",
        tasks=[
            TaskExtraction(
                title="Finalize migration plan",
                owner="Priya",
                priority="high",
                source_quote="Priya will own the migration plan.",
                confidence=0.9,
            )
        ],
    )

    with patch("app.services.extraction.get_llm_client") as mock_client:
        mock_client.return_value.extract = AsyncMock(return_value=result)
        pipeline = ExtractionPipeline(db_session, dispatcher=_NoopDispatcher())
        await pipeline.process(transcript.id)

    from sqlalchemy import select

    all_tasks = (
        await db_session.execute(select(Task).where(Task.project_id == project.id))
    ).scalars().all()
    assert len(all_tasks) == 1  # no duplicate created

    await db_session.refresh(existing)
    assert existing.owner == "Priya"  # gap-filled from the restatement

    activities = (
        await db_session.execute(
            select(TaskActivity).where(TaskActivity.task_id == existing_id)
        )
    ).scalars().all()
    assert any(a.kind == "dedup_merged" for a in activities)


@pytest.mark.asyncio
async def test_pipeline_marks_decision_superseded(db_session) -> None:
    project = Project(name="API Migration", slug="api-migration")
    db_session.add(project)
    await db_session.flush()
    old_decision = Decision(
        project_id=project.id,
        summary="Use a big-bang cutover",
        source_quote="We'll just cut over all at once.",
        confidence=0.8,
    )
    db_session.add(old_decision)
    await db_session.flush()
    old_id = old_decision.id

    transcript = Transcript(
        title="Migration sync — week 3",
        content="We changed our minds: staged cutover, not big-bang.\n\n" * 3,
        project_id=project.id,
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)

    result = ExtractionResult(
        project_hint="API Migration",
        summary="The team reversed the cutover decision.",
        decisions=[
            DecisionExtraction(
                summary="Adopt a staged cutover",
                supersedes=str(old_id),
                source_quote="staged cutover, not big-bang",
                confidence=0.9,
            )
        ],
    )

    with patch("app.services.extraction.get_llm_client") as mock_client:
        mock_client.return_value.extract = AsyncMock(return_value=result)
        pipeline = ExtractionPipeline(db_session, dispatcher=_NoopDispatcher())
        await pipeline.process(transcript.id)

    await db_session.refresh(old_decision)
    assert (old_decision.extra_metadata or {}).get("superseded") is True


@pytest.mark.asyncio
async def test_pipeline_marks_failed_on_llm_error(db_session) -> None:
    from app.llm.client import LLMError

    transcript = Transcript(title="t", content="some content " * 20)
    db_session.add(transcript)
    await db_session.commit()

    with patch("app.services.extraction.get_llm_client") as mock_client:
        mock_client.return_value.extract = AsyncMock(side_effect=LLMError("boom"))
        pipeline = ExtractionPipeline(db_session, dispatcher=_NoopDispatcher())
        result = await pipeline.process(transcript.id)

    assert result is None
    await db_session.refresh(transcript)
    assert transcript.status == "failed"
    assert transcript.error is not None
    assert "boom" in transcript.error
