"""Transcript ingestion and retrieval."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_ingest_key
from app.database import get_session
from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.risk import Risk
from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.transcript import (
    ExtractedBlockerOut,
    ExtractedDecisionOut,
    ExtractedRiskOut,
    ExtractedTaskOut,
    TranscriptCreate,
    TranscriptDetail,
    TranscriptList,
    TranscriptSummary,
)
from app.services.extraction import ExtractionPipeline
from app.services.project_resolver import get_or_create_project
from app.services.transcriber import MAX_BYTES, TranscriptionError, transcribe_path

router = APIRouter()


@router.post(
    "/upload",
    response_model=TranscriptDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ingest_key)],
    summary="Transcribe a video/audio file and run extraction",
)
async def upload_video(
    file: UploadFile = File(..., description="Video or audio file (mp4, mp3, m4a, wav, ogg, webm, flac). Max 1 GB."),
    title: str = Form(""),
    project_hint: str = Form(""),
    participants: str = Form("", description="Comma-separated participant names"),
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetail:
    filename = file.filename or "upload.mp4"
    ext = Path(filename).suffix or ".mp4"

    # Stream directly to disk — never load a 1 GB file into RAM
    with tempfile.TemporaryDirectory() as tmp_dir:
        upload_path = Path(tmp_dir) / f"upload{ext}"
        size = 0
        with upload_path.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):  # 1 MB at a time
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the 1 GB limit ({size / 1024**3:.2f} GB received so far).",
                    )
                fh.write(chunk)

        try:
            transcript_text = await transcribe_path(upload_path, filename)
        except TranscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    participant_list = [p.strip() for p in participants.split(",") if p.strip()] or None
    resolved_title = title.strip() or Path(filename).stem
    project = await get_or_create_project(session, project_hint.strip() or None)

    transcript = Transcript(
        title=resolved_title,
        content=transcript_text,
        source="video_upload",
        participants=participant_list,
        project_id=project.id if project else None,
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)

    pipeline = ExtractionPipeline(session)
    await pipeline.process(transcript.id)
    await session.refresh(transcript)
    return await _to_detail(session, transcript)


@router.post(
    "",
    response_model=TranscriptDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ingest_key)],
)
async def ingest_transcript(
    payload: TranscriptCreate,
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetail:
    """Ingest a transcript and (optionally) run extraction synchronously."""
    project = await get_or_create_project(session, payload.project_hint)
    transcript = Transcript(
        title=payload.title,
        content=payload.content,
        source=payload.source,
        meeting_date=payload.meeting_date,
        participants=payload.participants,
        project_id=project.id if project else None,
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)

    if payload.sync_extract:
        pipeline = ExtractionPipeline(session)
        await pipeline.process(transcript.id)
        await session.refresh(transcript)

    return await _to_detail(session, transcript)


@router.get("", response_model=TranscriptList)
async def list_transcripts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> TranscriptList:
    stmt = select(Transcript).order_by(Transcript.created_at.desc())
    if project_id is not None:
        stmt = stmt.where(Transcript.project_id == project_id)
    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return TranscriptList(
        items=[TranscriptSummary.model_validate(r) for r in rows],
        total=int(total or 0),
    )


@router.get("/{transcript_id}", response_model=TranscriptDetail)
async def get_transcript(
    transcript_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetail:
    stmt = (
        select(Transcript)
        .where(Transcript.id == transcript_id)
        .options(selectinload(Transcript.chunks))
    )
    transcript = (await session.execute(stmt)).scalars().first()
    if transcript is None:
        raise HTTPException(status_code=404, detail="transcript not found")
    return await _to_detail(session, transcript)


@router.post(
    "/{transcript_id}/reprocess",
    response_model=TranscriptDetail,
    dependencies=[Depends(require_ingest_key)],
)
async def reprocess_transcript(
    transcript_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetail:
    transcript = await session.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="transcript not found")
    # Wipe extracted artefacts so re-running doesn't duplicate them.
    for model in (Task, Decision, Risk, Blocker):
        rows = (
            await session.execute(
                select(model).where(model.transcript_id == transcript_id)
            )
        ).scalars().all()
        for row in rows:
            await session.delete(row)
    await session.commit()

    pipeline = ExtractionPipeline(session)
    await pipeline.process(transcript_id)
    await session.refresh(transcript)
    return await _to_detail(session, transcript)


async def _to_detail(session: AsyncSession, transcript: Transcript) -> TranscriptDetail:
    tasks = (
        await session.execute(
            select(Task).where(Task.transcript_id == transcript.id).order_by(Task.created_at)
        )
    ).scalars().all()
    decisions = (
        await session.execute(
            select(Decision)
            .where(Decision.transcript_id == transcript.id)
            .order_by(Decision.created_at)
        )
    ).scalars().all()
    risks = (
        await session.execute(
            select(Risk).where(Risk.transcript_id == transcript.id).order_by(Risk.created_at)
        )
    ).scalars().all()
    blockers = (
        await session.execute(
            select(Blocker)
            .where(Blocker.transcript_id == transcript.id)
            .order_by(Blocker.created_at)
        )
    ).scalars().all()

    return TranscriptDetail(
        id=transcript.id,
        title=transcript.title,
        status=transcript.status,
        source=transcript.source,
        project_id=transcript.project_id,
        meeting_date=transcript.meeting_date,
        processed_at=transcript.processed_at,
        created_at=transcript.created_at,
        content=transcript.content,
        participants=transcript.participants,
        error=transcript.error,
        tasks=[ExtractedTaskOut.model_validate(t) for t in tasks],
        decisions=[ExtractedDecisionOut.model_validate(d) for d in decisions],
        risks=[ExtractedRiskOut.model_validate(r) for r in risks],
        blockers=[ExtractedBlockerOut.model_validate(b) for b in blockers],
    )
