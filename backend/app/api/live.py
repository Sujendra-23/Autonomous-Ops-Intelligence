"""Live note-taker — stream meeting audio in, get structured notes out.

Flow:

1. `POST /api/live/sessions` creates a `Transcript` row with status ``live`` and
   resolves a project from the hint (so cross-meeting context works from the
   first second).
2. The browser extension opens the WebSocket `/api/live/ws/{transcript_id}` and
   streams 16 kHz mono PCM16 audio frames (binary).
3. The backend relays audio to the STT provider, appends finalized text to the
   transcript, and — on a debounce (`LiveSession`) — re-runs extraction
   incrementally, streaming the current notes snapshot back as JSON.
4. A `{"type": "finalize"}` text frame (or disconnect) ends the meeting: one
   full pass runs embeddings + integration dispatch, then status flips to
   ``completed``.

Only one task touches the database session at a time (the consumer), so the
non-thread-safe `AsyncSession` is never used concurrently.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ingest_key
from app.config import get_settings
from app.database import SessionLocal, get_session
from app.logging import get_logger
from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.risk import Risk
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.extraction import ExtractionPipeline
from app.services.live_session import LiveSession
from app.services.live_stt import get_transcriber
from app.services.project_resolver import get_or_create_project

logger = get_logger("app.api.live")
router = APIRouter()


class LiveSessionCreate(BaseModel):
    title: str = "Live meeting"
    project_hint: str | None = None
    participants: list[str] | None = None


class LiveSessionOut(BaseModel):
    transcript_id: uuid.UUID
    ws_path: str
    stt_enabled: bool


@router.post(
    "/sessions",
    response_model=LiveSessionOut,
    dependencies=[Depends(require_ingest_key)],
    summary="Open a live meeting session",
)
async def create_live_session(
    payload: LiveSessionCreate,
    session: AsyncSession = Depends(get_session),
) -> LiveSessionOut:
    project = await get_or_create_project(session, payload.project_hint)
    transcript = Transcript(
        title=payload.title.strip() or "Live meeting",
        content="",
        source="live",
        participants=payload.participants,
        project_id=project.id if project else None,
        status="live",
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)
    return LiveSessionOut(
        transcript_id=transcript.id,
        ws_path=f"/api/live/ws/{transcript.id}",
        stt_enabled=get_settings().stt_enabled,
    )


@router.websocket("/ws/{transcript_id}")
async def live_ws(websocket: WebSocket, transcript_id: uuid.UUID) -> None:
    settings = get_settings()

    expected = settings.ingest_api_key.get_secret_value()
    if expected and websocket.query_params.get("api_key") != expected:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    async with SessionLocal() as db, get_transcriber() as transcriber:
        transcript = await db.get(Transcript, transcript_id)
        if transcript is None:
            await _safe_send(websocket, {"type": "error", "detail": "unknown session"})
            await _safe_close(websocket)
            return

        live = LiveSession(
            min_chars=settings.live_min_chars,
            min_interval_s=settings.live_min_interval_seconds,
            max_interval_s=settings.live_max_interval_seconds,
        )
        pipeline = ExtractionPipeline(db)
        loop = asyncio.get_event_loop()
        stop = asyncio.Event()

        await _safe_send(
            websocket,
            {
                "type": "ready",
                "transcript_id": str(transcript_id),
                "stt_enabled": settings.stt_enabled,
            },
        )

        async def run_extraction(*, dispatch: bool) -> None:
            transcript.content = live.full_text()
            await db.commit()
            if not transcript.content:
                return
            if dispatch:
                await pipeline.process(transcript_id)
            else:
                await pipeline.extract_incremental(transcript_id, dispatch=False)
            await _safe_send(websocket, {"type": "notes", **await _snapshot(db, transcript_id)})

        async def consume() -> None:
            try:
                async for ev in transcriber.events():
                    if stop.is_set():
                        break
                    if ev.is_final:
                        live.add_final(ev.text)
                    else:
                        live.set_interim(ev.text)
                    await _safe_send(
                        websocket,
                        {"type": "transcript", "text": ev.text, "is_final": ev.is_final},
                    )
                    if ev.is_final and live.should_extract(loop.time()):
                        await run_extraction(dispatch=False)
                        live.mark_extracted(loop.time())
            except WebSocketDisconnect:
                stop.set()
            except Exception:
                logger.exception("live.consume_error", transcript_id=str(transcript_id))

        async def receive_loop() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    data = message.get("bytes")
                    if data is not None:
                        await transcriber.send_audio(data)
                        continue
                    text = message.get("text")
                    if text is not None:
                        with contextlib.suppress(ValueError):
                            if json.loads(text).get("type") == "finalize":
                                break
            finally:
                stop.set()

        recv_task = asyncio.create_task(receive_loop())
        cons_task = asyncio.create_task(consume())
        try:
            await recv_task
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()
            # Closing the transcriber ends the events() async-iteration so the
            # consumer task can finish before we run the final pass.
            await transcriber.close()
            await cons_task

        with contextlib.suppress(Exception):
            if live.full_text():
                await run_extraction(dispatch=True)
            await _safe_send(websocket, {"type": "done", "transcript_id": str(transcript_id)})

        fresh = await db.get(Transcript, transcript_id)
        if fresh is not None and fresh.status == "live":
            fresh.status = "completed"
            await db.commit()

    await _safe_close(websocket)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


async def _snapshot(db: AsyncSession, transcript_id: uuid.UUID) -> dict:
    """Current structured notes for the transcript, for streaming to the UI."""

    async def rows(model):
        return (
            await db.execute(
                select(model)
                .where(model.transcript_id == transcript_id)
                .order_by(model.created_at)
            )
        ).scalars().all()

    tasks = await rows(Task)
    decisions = await rows(Decision)
    risks = await rows(Risk)
    blockers = await rows(Blocker)
    return {
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "owner": t.owner,
                "status": t.status,
                "priority": t.priority,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "source_quote": t.source_quote,
                "confidence": t.confidence,
            }
            for t in tasks
        ],
        "decisions": [
            {
                "id": str(d.id),
                "summary": d.summary,
                "decided_by": d.decided_by,
                "source_quote": d.source_quote,
            }
            for d in decisions
        ],
        "risks": [
            {"id": str(r.id), "title": r.title, "severity": r.severity, "likelihood": r.likelihood}
            for r in risks
        ],
        "blockers": [
            {
                "id": str(b.id),
                "summary": b.summary,
                "blocked_party": b.blocked_party,
                "needs_from": b.needs_from,
                "severity": b.severity,
                "status": b.status,
            }
            for b in blockers
        ],
    }


async def _safe_send(websocket: WebSocket, payload: dict) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json(payload)


async def _safe_close(websocket: WebSocket) -> None:
    with contextlib.suppress(Exception):
        await websocket.close()
