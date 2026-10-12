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
from app.config import get_settings
from app.database import get_session
from app.integrations.calendar import meeting_context, resolve_meeting_project
from app.llm.client import LLMError, get_llm_client
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
from app.services.documents import (
    MAX_DOCUMENT_BYTES,
    DocumentError,
    extract_document,
    result_to_text,
)
from app.services.extraction import ExtractionPipeline
from app.services.project_resolver import get_or_create_project
from app.services.transcriber import MAX_BYTES, TranscriptionError, transcribe_path
from app.services.video_frames import FrameSamplingError, recording_to_text

router = APIRouter()


@router.post(
    "/upload",
    response_model=TranscriptDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ingest_key)],
    summary="Transcribe a video/audio file and run extraction",
)
async def upload_video(
    file: UploadFile = File(
        ..., description="Video or audio file (mp4, mp3, m4a, wav, ogg, webm, flac). Max 100 MB."
    ),
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
                        detail=(
                            f"File exceeds the 100 MB limit "
                            f"({size / 1024**3:.2f} GB received so far)."
                        ),
                    )
                fh.write(chunk)

        try:
            transcript_text = await transcribe_path(upload_path, filename)
        except TranscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    if get_settings().auth_mode != "oidc":
        await pipeline.process(transcript.id)
    await session.refresh(transcript)
    return await _to_detail(session, transcript)


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(
                status_code=413, detail=f"File exceeds the {limit // 1024 // 1024} MB limit."
            )
    return bytes(data)


def _reject_in_saas_mode() -> None:
    if get_settings().auth_mode == "oidc":
        raise HTTPException(
            status_code=501,
            detail="Document and recording ingestion is not available in SaaS mode yet.",
        )


@router.post(
    "/upload-document",
    response_model=TranscriptDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ingest_key)],
    summary="Extract tasks, decisions and risks from a scanned PDF or image",
)
async def upload_document(
    file: UploadFile = File(
        ..., description="PDF or PNG/JPEG/WebP of an agenda, minutes or action list. Max 20 MB."
    ),
    title: str = Form(""),
    project_hint: str = Form(""),
    participants: str = Form("", description="Comma-separated participant names"),
    meeting_date: str = Form("", description="ISO date, used to resolve relative deadlines"),
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetail:
    _reject_in_saas_mode()
    filename = file.filename or "document.pdf"
    data = await _read_limited(file, MAX_DOCUMENT_BYTES)
    participant_list = [p.strip() for p in participants.split(",") if p.strip()] or None
    resolved_title = title.strip() or Path(filename).stem
    try:
        result = await extract_document(
            get_llm_client(),
            filename,
            data,
            meeting_title=resolved_title,
            meeting_date=meeting_date.strip() or None,
            participants=participant_list,
            project_hint=project_hint.strip() or None,
        )
    except DocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Vision extraction failed: {exc}") from exc

    project = await get_or_create_project(
        session, project_hint.strip() or result.project_hint
    )
    transcript = Transcript(
        title=resolved_title,
        content=result_to_text(result),
        source="document_upload",
        participants=participant_list,
        project_id=project.id if project else None,
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)

    await ExtractionPipeline(session).process(transcript.id, precomputed=result)
    await session.refresh(transcript)
    return await _to_detail(session, transcript)


@router.post(
    "/upload-recording",
    response_model=TranscriptDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ingest_key)],
    summary="Caption a screen recording's keyframes and run extraction on the captions",
)
async def upload_recording(
    file: UploadFile = File(..., description="Screen recording (mp4, webm). Max 100 MB."),
    title: str = Form(""),
    project_hint: str = Form(""),
    participants: str = Form("", description="Comma-separated participant names"),
    frame_mode: str = Form("scene", pattern="^(scene|interval)$"),
    interval_seconds: float = Form(30.0, ge=1.0, le=600.0),
    include_audio: bool = Form(False, description="Also transcribe the audio track (Whisper)"),
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetail:
    _reject_in_saas_mode()
    filename = file.filename or "recording.mp4"
    ext = Path(filename).suffix.lower() or ".mp4"
    with tempfile.TemporaryDirectory() as tmp_dir:
        upload_path = Path(tmp_dir) / f"upload{ext}"
        size = 0
        with upload_path.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 100 MB limit.")
                fh.write(chunk)
        try:
            audio_text = await transcribe_path(upload_path, filename) if include_audio else None
            text = await recording_to_text(
                get_llm_client(),
                upload_path,
                audio_transcript=audio_text,
                mode=frame_mode,
                interval=interval_seconds,
            )
        except (TranscriptionError, FrameSamplingError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=f"Captioning failed: {exc}") from exc

    participant_list = [p.strip() for p in participants.split(",") if p.strip()] or None
    project = await get_or_create_project(session, project_hint.strip() or None)
    transcript = Transcript(
        title=title.strip() or Path(filename).stem,
        content=text,
        source="screen_recording",
        participants=participant_list,
        project_id=project.id if project else None,
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)

    await ExtractionPipeline(session).process(transcript.id)
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
    context = await meeting_context(payload.calendar_event_id)
    project = await resolve_meeting_project(session, context, payload.project_hint)
    transcript = Transcript(
        title=context.get("title") or payload.title,
        content=payload.content,
        source=payload.source,
        meeting_date=payload.meeting_date or context.get("meeting_date"),
        participants=payload.participants
        if payload.participants is not None
        else context.get("participants"),
        calendar_context=context.get("calendar_context"),
        project_id=project.id if project else None,
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)

    if payload.sync_extract and get_settings().auth_mode != "oidc":
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
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return TranscriptList(
        items=[TranscriptSummary.model_validate(r) for r in rows],
        total=int(total or 0),
    )


@router.get("/{transcript_id}/status")
async def transcript_status(
    transcript_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    transcript = await session.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(404, "Transcript not found")
    return {"id": str(transcript.id), "status": transcript.status}


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
    if get_settings().auth_mode == "oidc":
        if transcript.status not in ("failed", "completed"):
            raise HTTPException(409, "Transcript is already queued or processing")
        raise HTTPException(
            409, "SaaS reprocessing is disabled to prevent duplicate external tasks"
        )
    # Wipe extracted artefacts so re-running doesn't duplicate them.
    for model in (Task, Decision, Risk, Blocker):
        rows = (
            (await session.execute(select(model).where(model.transcript_id == transcript_id)))
            .scalars()
            .all()
        )
        for row in rows:
            await session.delete(row)
    await session.commit()

    pipeline = ExtractionPipeline(session)
    await pipeline.process(transcript_id)
    await session.refresh(transcript)
    return await _to_detail(session, transcript)


async def _to_detail(session: AsyncSession, transcript: Transcript) -> TranscriptDetail:
    tasks = (
        (
            await session.execute(
                select(Task).where(Task.transcript_id == transcript.id).order_by(Task.created_at)
            )
        )
        .scalars()
        .all()
    )
    decisions = (
        (
            await session.execute(
                select(Decision)
                .where(Decision.transcript_id == transcript.id)
                .order_by(Decision.created_at)
            )
        )
        .scalars()
        .all()
    )
    risks = (
        (
            await session.execute(
                select(Risk).where(Risk.transcript_id == transcript.id).order_by(Risk.created_at)
            )
        )
        .scalars()
        .all()
    )
    blockers = (
        (
            await session.execute(
                select(Blocker)
                .where(Blocker.transcript_id == transcript.id)
                .order_by(Blocker.created_at)
            )
        )
        .scalars()
        .all()
    )

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
        calendar_context=transcript.calendar_context,
        error=transcript.error,
        tasks=[ExtractedTaskOut.model_validate(t) for t in tasks],
        decisions=[ExtractedDecisionOut.model_validate(d) for d in decisions],
        risks=[ExtractedRiskOut.model_validate(r) for r in risks],
        blockers=[ExtractedBlockerOut.model_validate(b) for b in blockers],
    )
