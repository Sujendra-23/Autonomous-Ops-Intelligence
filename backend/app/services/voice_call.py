"""One phone call: Twilio media frames -> STT relay -> voice agent -> spoken reply.

This is the glue between the Media Streams WebSocket (`app/api/voice.py`) and the
pieces that have no I/O of their own. It deliberately reuses `StreamingTranscriber`
from `live_stt.py`, so any configured STT provider works for phone audio too.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import time

from app.integrations.twilio_voice import CallResponder
from app.logging import get_logger
from app.services.audio import TwilioAudioConverter
from app.services.live_stt import StreamingTranscriber
from app.services.voice_agent import CallState, VoiceAgent

logger = get_logger("app.services.voice_call")

# Wait this long after the last final transcript segment before treating the
# caller's turn as complete (a pause mid-sentence yields several finals).
UTTERANCE_GAP_SECONDS = 1.2
SESSION_TTL_SECONDS = 2 * 3600
MAX_FRAME_BYTES = 4096


class VoiceSessions:
    """In-memory per-call state, keyed by CallSid.

    A call's media stream is re-opened after each spoken reply, so state must outlive a
    single WebSocket. This is process-local: run one backend replica (or sticky routing)
    for voice, or move this to Redis.
    """

    def __init__(self) -> None:
        self._states: dict[str, CallState] = {}

    def get_or_create(self, call_sid: str, caller: str = "") -> CallState:
        now = time.monotonic()
        stale = [s for s, st in self._states.items() if now - st.created_at > SESSION_TTL_SECONDS]
        for sid in stale:
            del self._states[sid]
        state = self._states.get(call_sid)
        if state is None:
            state = self._states[call_sid] = CallState(
                call_sid=call_sid, caller=caller, created_at=now
            )
        return state

    def discard(self, call_sid: str) -> None:
        self._states.pop(call_sid, None)


class VoiceCall:
    def __init__(
        self,
        *,
        state: CallState,
        transcriber: StreamingTranscriber,
        agent: VoiceAgent,
        responder: CallResponder,
        gap_seconds: float = UTTERANCE_GAP_SECONDS,
    ) -> None:
        self.state = state
        self._transcriber = transcriber
        self._agent = agent
        self._responder = responder
        self._gap = gap_seconds
        self._converter = TwilioAudioConverter(transcriber.sample_rate)

    async def on_media(self, payload_b64: str) -> None:
        try:
            audio = base64.b64decode(payload_b64, validate=True)
        except (binascii.Error, ValueError):
            return
        if not audio or len(audio) > MAX_FRAME_BYTES:
            return
        await self._transcriber.send_audio(self._converter.convert(audio))

    async def consume(self) -> None:
        """Drain STT events; hand each completed caller turn to the agent."""
        events = self._transcriber.events().__aiter__()
        pending: list[str] = []
        next_event = asyncio.ensure_future(events.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({next_event}, timeout=self._gap if pending else None)
                if not done:
                    await self._flush(pending)
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break
                if event.is_final and event.text.strip():
                    pending.append(event.text.strip())
                next_event = asyncio.ensure_future(events.__anext__())
        finally:
            if not next_event.done():
                next_event.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_event
        await self._flush(pending)

    async def _flush(self, pending: list[str]) -> None:
        if not pending:
            return
        text, pending[:] = " ".join(pending), []
        outcome = await self._agent.handle_utterance(self.state, text)
        if not outcome.reply:
            return
        try:
            await self._responder.reply(
                self.state.call_sid, outcome.reply, reconnect=not outcome.done
            )
        except Exception:
            # A failed update leaves the caller in silence; surface it in logs, not the call.
            logger.exception("voice.reply_failed", call=self.state.call_sid)
