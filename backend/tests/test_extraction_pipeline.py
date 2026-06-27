"""End-to-end extraction with a stubbed LLM and stubbed integrations.

Skipped when Postgres+pgvector isn't reachable (see `conftest.db_available`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.integrations.base import DispatchResult
from app.integrations.dispatcher import IntegrationDispatcher
from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.extraction import (
    BlockerExtraction,
    DecisionExtraction,
    ExtractionResult,
    RiskExtraction,
    TaskExtraction,
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
