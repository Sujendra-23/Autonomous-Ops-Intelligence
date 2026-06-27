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

from app.config import get_settings
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
from app.schemas.extraction import (
    DecisionExtraction,
    ExtractionResult,
    TaskExtraction,
    TaskUpdateExtraction,
)
from app.services.chunker import chunk_transcript
from app.services.context import build_prior_context
from app.services.dedup import find_duplicate
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
            prior_context = await self._build_prior_context(transcript)
            result = await self._extract(transcript, prior_context)

            project = await self._resolve_project(transcript, result)
            updates_applied = await self._apply_task_updates(transcript, project, result)
            counts = await self._persist_artefacts(transcript, project, result)

            transcript.status = "completed"
            transcript.processed_at = datetime.utcnow()
            transcript.error = None
            await self._session.commit()
            log.info(
                "extraction.completed",
                tasks=len(result.tasks),
                tasks_created=counts["tasks_created"],
                tasks_deduped=counts["tasks_deduped"],
                task_updates=updates_applied,
                decisions=len(result.decisions),
                decisions_superseded=counts["decisions_superseded"],
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

    async def _build_prior_context(self, transcript: Transcript) -> str | None:
        """Render the project's existing state into a prompt block, if enabled.

        Only fires when the transcript is already tied to a project (the common
        case — `get_or_create_project` runs at ingest from `project_hint`). The
        first meeting of a project has no prior state, so this returns None.
        """
        settings = get_settings()
        if not settings.cross_meeting_context or transcript.project_id is None:
            return None
        ctx = await build_prior_context(
            self._session,
            transcript.project_id,
            max_tasks=settings.context_max_tasks,
            max_decisions=settings.context_max_decisions,
            max_blockers=settings.context_max_blockers,
        )
        if ctx.is_empty():
            return None
        logger.info(
            "extraction.prior_context",
            transcript_id=str(transcript.id),
            open_tasks=len(ctx.open_tasks),
            decisions=len(ctx.recent_decisions),
            blockers=len(ctx.open_blockers),
        )
        return ctx.to_prompt_block()

    async def _extract(
        self,
        transcript: Transcript,
        prior_context: str | None = None,
    ) -> ExtractionResult:
        client = get_llm_client()
        meeting_date = (
            transcript.meeting_date.isoformat() if transcript.meeting_date else None
        )
        return await client.extract(
            transcript.content,
            meeting_title=transcript.title,
            meeting_date=meeting_date,
            participants=transcript.participants,
            prior_context=prior_context,
        )

    async def _apply_task_updates(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> int:
        """Apply status/progress updates the model raised against existing tasks."""
        if not result.task_updates:
            return 0
        now = datetime.utcnow()
        applied = 0
        for upd in result.task_updates:
            task = await self._resolve_referenced_task(upd, project)
            if task is None:
                continue

            changed: dict[str, object] = {}
            if upd.new_status and upd.new_status != task.status:
                changed["status"] = {"from": task.status, "to": upd.new_status}
                task.status = upd.new_status
                task.last_status_change_at = now

            if not changed and not upd.note:
                continue

            self._session.add(
                TaskActivity(
                    task_id=task.id,
                    kind="status_change" if changed else "progress_note",
                    payload={
                        **changed,
                        "note": upd.note,
                        "source_quote": upd.source_quote,
                        "confidence": upd.confidence,
                        "from_transcript": str(transcript.id),
                    },
                    actor="extractor",
                )
            )
            applied += 1
        return applied

    async def _resolve_referenced_task(
        self,
        upd: TaskUpdateExtraction,
        project: Project | None,
    ) -> Task | None:
        """Validate a model-supplied task id and return the row, or None."""
        try:
            task_uuid = uuid.UUID(str(upd.task_id))
        except (ValueError, AttributeError):
            logger.warning("extraction.task_update_bad_id", task_id=upd.task_id)
            return None
        task = await self._session.get(Task, task_uuid)
        if task is None:
            logger.warning("extraction.task_update_missing", task_id=str(task_uuid))
            return None
        if project is not None and task.project_id != project.id:
            logger.warning("extraction.task_update_cross_project", task_id=str(task_uuid))
            return None
        return task

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
    ) -> dict[str, int]:
        settings = get_settings()
        project_id = project.id if project else None
        counts = {"tasks_created": 0, "tasks_deduped": 0, "decisions_superseded": 0}

        # ---- Tasks (de-duplicated against the project's existing open tasks) ----
        existing_by_id: dict[uuid.UUID, Task] = {}
        candidates: list[tuple[uuid.UUID, str]] = []
        if project_id is not None:
            existing = (
                await self._session.execute(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.status.in_(("open", "in_progress", "blocked")),
                    )
                )
            ).scalars().all()
            for t in existing:
                existing_by_id[t.id] = t
                candidates.append((t.id, t.title))

        for task in result.tasks:
            dup_id = (
                find_duplicate(
                    task.title, candidates, threshold=settings.dedup_title_threshold
                )
                if candidates
                else None
            )
            if dup_id is not None:
                await self._merge_duplicate_task(existing_by_id[dup_id], task, transcript)
                counts["tasks_deduped"] += 1
                continue

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
            counts["tasks_created"] += 1
            # Let later tasks in this same meeting dedup against this new one too.
            existing_by_id[row.id] = row
            candidates.append((row.id, row.title))

        for decision in result.decisions:
            if decision.supersedes and await self._mark_decision_superseded(
                decision, project, transcript
            ):
                counts["decisions_superseded"] += 1
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

        return counts

    async def _merge_duplicate_task(
        self,
        existing: Task,
        incoming: TaskExtraction,
        transcript: Transcript,
    ) -> None:
        """A re-stated task — enrich the existing row instead of duplicating it.

        We only fill gaps (a previously-unknown owner or due date); we never
        overwrite human/earlier values, and we log the merge for auditability.
        """
        enriched: dict[str, object] = {}
        if not existing.owner and incoming.owner:
            existing.owner = incoming.owner
            enriched["owner"] = incoming.owner
        if existing.due_date is None and incoming.due_date is not None:
            existing.due_date = incoming.due_date
            enriched["due_date"] = incoming.due_date.isoformat()
        self._session.add(
            TaskActivity(
                task_id=existing.id,
                kind="dedup_merged",
                payload={
                    "from_transcript": str(transcript.id),
                    "restated_title": incoming.title,
                    "source_quote": incoming.source_quote,
                    "enriched": enriched,
                },
                actor="extractor",
            )
        )

    async def _mark_decision_superseded(
        self,
        incoming: DecisionExtraction,
        project: Project | None,
        transcript: Transcript,
    ) -> bool:
        """Flag the decision referenced by `incoming.supersedes` as superseded."""
        try:
            dec_uuid = uuid.UUID(str(incoming.supersedes))
        except (ValueError, AttributeError):
            return False
        old = await self._session.get(Decision, dec_uuid)
        if old is None:
            return False
        if project is not None and old.project_id != project.id:
            return False
        meta = dict(old.extra_metadata or {})
        meta["superseded"] = True
        meta["superseded_at"] = datetime.utcnow().isoformat()
        meta["superseded_by_transcript"] = str(transcript.id)
        meta["superseded_reason"] = incoming.summary
        old.extra_metadata = meta
        return True

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
