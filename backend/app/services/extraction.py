"""Transcript → structured ops data extraction pipeline.

Flow:

  1. Mark transcript `chunking`
  2. Chunk content, embed each chunk, persist chunks (vector store ready)
  3. Mark transcript `extracting`, call the LLM with the full text
  4. Validate the structured response, persist tasks/decisions/risks/blockers
  5. Dispatch each artefact to enabled integrations (Notion/Linear/Slack)
  6. Mark transcript `completed`

The function is idempotent on the input transcript row — we wrap state changes
in their own commits so a partial failure leaves clear breadcrumbs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.dispatcher import IntegrationDispatcher
from app.llm.client import LLMError, get_llm_client
from app.llm.embeddings import get_embedding_client
from app.logging import get_logger
from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task, TaskActivity
from app.models.transcript import Transcript, TranscriptChunk
from app.schemas.extraction import ExtractionResult
from app.services.chunker import chunk_transcript
from app.services.project_resolver import get_or_create_project

logger = get_logger("app.services.extraction")


class ExtractionPipeline:
    def __init__(
        self,
        session: AsyncSession,
        *,
        dispatcher: IntegrationDispatcher | None = None,
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher or IntegrationDispatcher()

    async def process(self, transcript_id: uuid.UUID) -> ExtractionResult | None:
        transcript = await self._load(transcript_id)
        if transcript is None:
            logger.warning("extraction.missing_transcript", transcript_id=str(transcript_id))
            return None

        log = logger.bind(transcript_id=str(transcript.id), title=transcript.title)
        try:
            await self._set_status(transcript, "chunking")
            await self._chunk_and_embed(transcript)

            await self._set_status(transcript, "extracting")
            result = await self._extract(transcript)

            project = await self._resolve_project(transcript, result)
            await self._persist_artefacts(transcript, project, result)

            transcript.status = "completed"
            transcript.processed_at = datetime.utcnow()
            transcript.error = None
            await self._session.commit()
            log.info(
                "extraction.completed",
                tasks=len(result.tasks),
                decisions=len(result.decisions),
                risks=len(result.risks),
                blockers=len(result.blockers),
            )

            await self._dispatch(transcript, project, result)
            return result

        except LLMError as exc:
            await self._session.rollback()
            await self._mark_failed(transcript_id, f"LLM error: {exc}")
            log.error("extraction.llm_error", error=str(exc))
            return None
        except Exception as exc:
            await self._session.rollback()
            await self._mark_failed(transcript_id, f"Pipeline error: {exc}")
            log.exception("extraction.unexpected_error")
            return None

    # --------------------------------------------------------------------- #
    # Stages                                                                #
    # --------------------------------------------------------------------- #

    async def _load(self, transcript_id: uuid.UUID) -> Transcript | None:
        return await self._session.get(Transcript, transcript_id)

    async def _set_status(self, transcript: Transcript, status: str) -> None:
        transcript.status = status
        await self._session.commit()
        await self._session.refresh(transcript)

    async def _mark_failed(self, transcript_id: uuid.UUID, reason: str) -> None:
        transcript = await self._session.get(Transcript, transcript_id)
        if transcript is None:
            return
        transcript.status = "failed"
        transcript.error = reason[:2000]
        await self._session.commit()

    async def _chunk_and_embed(self, transcript: Transcript) -> None:
        chunks = chunk_transcript(transcript.content)
        if not chunks:
            return

        # Reset chunks for re-runs.
        existing = await self._session.execute(
            select(TranscriptChunk).where(TranscriptChunk.transcript_id == transcript.id)
        )
        for row in existing.scalars():
            await self._session.delete(row)
        await self._session.flush()

        embedder = get_embedding_client()
        try:
            vectors = await embedder.embed([c.content for c in chunks])
        except Exception as exc:  # noqa: BLE001
            logger.warning("extraction.embedding_failed", error=str(exc))
            vectors = [None] * len(chunks)

        for chunk, vec in zip(chunks, vectors, strict=False):
            self._session.add(
                TranscriptChunk(
                    transcript_id=transcript.id,
                    index=chunk.index,
                    content=chunk.content,
                    token_estimate=chunk.token_estimate,
                    embedding=vec,
                )
            )
        await self._session.commit()

    async def _extract(self, transcript: Transcript) -> ExtractionResult:
        client = get_llm_client()
        meeting_date = (
            transcript.meeting_date.isoformat() if transcript.meeting_date else None
        )
        return await client.extract(
            transcript.content,
            meeting_title=transcript.title,
            meeting_date=meeting_date,
            participants=transcript.participants,
        )

    async def _resolve_project(
        self,
        transcript: Transcript,
        result: ExtractionResult,
    ) -> Project | None:
        if transcript.project_id is not None:
            return await self._session.get(Project, transcript.project_id)
        project = await get_or_create_project(self._session, result.project_hint)
        if project is not None:
            transcript.project_id = project.id
        return project

    async def _persist_artefacts(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> None:
        project_id = project.id if project else None
        for task in result.tasks:
            row = Task(
                project_id=project_id,
                transcript_id=transcript.id,
                title=task.title,
                description=task.description,
                owner=task.owner,
                due_date=task.due_date,
                priority=task.priority,
                source_quote=task.source_quote,
                confidence=task.confidence,
            )
            self._session.add(row)
            await self._session.flush()
            self._session.add(
                TaskActivity(
                    task_id=row.id,
                    kind="created",
                    payload={"source": "extraction"},
                    actor="extractor",
                )
            )

        for decision in result.decisions:
            self._session.add(
                Decision(
                    project_id=project_id,
                    transcript_id=transcript.id,
                    summary=decision.summary,
                    rationale=decision.rationale,
                    decided_by=decision.decided_by,
                    source_quote=decision.source_quote,
                    confidence=decision.confidence,
                )
            )

        for risk in result.risks:
            self._session.add(
                Risk(
                    project_id=project_id,
                    transcript_id=transcript.id,
                    title=risk.title,
                    description=risk.description,
                    severity=risk.severity,
                    likelihood=risk.likelihood,
                    mitigation=risk.mitigation,
                    source_quote=risk.source_quote,
                    confidence=risk.confidence,
                )
            )

        for blocker in result.blockers:
            self._session.add(
                Blocker(
                    project_id=project_id,
                    transcript_id=transcript.id,
                    summary=blocker.summary,
                    description=blocker.description,
                    blocked_party=blocker.blocked_party,
                    needs_from=blocker.needs_from,
                    severity=blocker.severity,
                    source_quote=blocker.source_quote,
                    confidence=blocker.confidence,
                )
            )

    async def _dispatch(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> None:
        try:
            await self._dispatcher.publish(
                session=self._session,
                transcript=transcript,
                project=project,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("extraction.dispatch_failed", error=str(exc))
