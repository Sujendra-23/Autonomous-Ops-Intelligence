"""Streaming speech-to-text for the live note-taker.

The browser extension captures the meeting tab's audio and streams 16 kHz mono
PCM16 frames to our WebSocket; we relay those frames to a streaming STT provider
and surface interim/final transcripts back. Keeping the relay server-side means
the **provider API key never leaves the backend** — it stays a `SecretStr`,
consistent with the rest of the project's secret handling.

Providers sit behind `StreamingTranscriber` so the live API doesn't care which
one is wired. Deepgram is implemented (a stable, low-latency WebSocket STT);
`NullTranscriber` is the no-key fallback so the endpoint still runs in dev (it
just produces no text). OpenAI Realtime is also supported at 24 kHz.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlencode

from app.config import get_settings
from app.logging import get_logger
from app.services.dlp import redact
from app.services.transcription_languages import normalize_language

logger = get_logger("app.services.live_stt")

# Default capture rate. Each provider exposes its own `sample_rate`; the live
# session endpoint reports the configured one to the extension so the
# AudioWorklet captures at the rate the provider expects.
SAMPLE_RATE = 16000
ENCODING = "linear16"
CHANNELS = 1


@dataclass
class TranscriptEvent:
    text: str
    is_final: bool
    confidence: float | None = None
    needs_review: bool = False


class StreamingTranscriber(ABC):
    """A live STT session: feed audio in, async-iterate transcripts out."""

    # Capture sample rate the provider expects (reported to the extension).
    sample_rate: int = SAMPLE_RATE

    async def __aenter__(self) -> StreamingTranscriber:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None: ...

    @abstractmethod
    def events(self) -> AsyncIterator[TranscriptEvent]: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def finish(self) -> None:
        """Flush final audio while the events consumer is still running, then close."""
        await self.close()


class NullTranscriber(StreamingTranscriber):
    """No-op fallback used when no STT provider is configured."""

    async def connect(self) -> None:
        logger.warning("live_stt.no_provider", detail="STT not configured; no transcription")

    async def send_audio(self, chunk: bytes) -> None:
        return None

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        return
        yield  # pragma: no cover — makes this an async generator

    async def close(self) -> None:
        return None


class DeepgramTranscriber(StreamingTranscriber):
    """Deepgram streaming STT over WebSocket.

    Auth uses the WebSocket subprotocol (`token, <key>`) rather than a header,
    which is stable across `websockets` versions. Audio in is raw PCM16; results
    come back as JSON with interim (`is_final=false`) and final segments.
    """

    _URL = "wss://api.deepgram.com/v1/listen"

    def __init__(
        self, api_key: str, language: str = "auto", vocabulary: list[str] | None = None
    ) -> None:
        self._api_key = api_key
        self._language = normalize_language(language)
        self._vocabulary = [redact(term) for term in (vocabulary or [])[:50]]
        self._ws = None
        self._finish_ack = asyncio.Event()

    async def connect(self) -> None:
        # Imported lazily so the module (and its tests) load without the optional
        # `websockets` dependency present.
        from websockets.asyncio.client import connect

        query = urlencode(
            {
                "encoding": ENCODING,
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "model": "nova-3",
                "language": "multi" if self._language == "auto" else self._language,
                "interim_results": "true",
                "smart_format": "true",
                "punctuate": "true",
                "filler_words": "true",
                "keyterm": self._vocabulary,
            },
            doseq=True,
        )
        self._ws = await connect(f"{self._URL}?{query}", subprotocols=["token", self._api_key])
        logger.info("live_stt.connected", provider="deepgram")

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is not None and chunk:
            await self._ws.send(chunk)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        if self._ws is None:
            return
        async for message in self._ws:
            if isinstance(message, bytes):
                continue
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                continue
            if data.get("type") == "Metadata":
                self._finish_ack.set()
            channel = data.get("channel")
            if not channel:
                continue
            alts = channel.get("alternatives") or []
            text = (alts[0].get("transcript") if alts else "") or ""
            if text.strip():
                confidence = alts[0].get("confidence") if alts else None
                confidence = (
                    confidence
                    if isinstance(confidence, int | float)
                    and math.isfinite(confidence)
                    and 0 <= confidence <= 1
                    else None
                )
                weak_words = any(
                    isinstance(word.get("confidence"), int | float) and word["confidence"] < 0.65
                    for word in (alts[0].get("words") or [])
                )
                yield TranscriptEvent(
                    text=text.strip(),
                    is_final=bool(data.get("is_final")),
                    confidence=confidence,
                    needs_review=weak_words or confidence is not None and confidence < 0.85,
                )

    async def finish(self) -> None:
        if self._ws is None:
            return
        try:
            self._finish_ack.clear()
            await self._ws.send(json.dumps({"type": "CloseStream"}))
            await asyncio.wait_for(self._finish_ack.wait(), timeout=20)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._ws is None:
            return
        # Best-effort flush; ignore failures on teardown.
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        try:
            await self._ws.close()
        finally:
            self._ws = None


class OpenAIRealtimeTranscriber(StreamingTranscriber):
    """OpenAI Realtime transcription over WebSocket.

    An alternative to Deepgram for teams already standardized on OpenAI. Audio is
    sent as base64 PCM16 via `input_audio_buffer.append`; transcripts arrive as
    `input_audio_transcription` delta (interim) / completed (final) events.

    The Realtime API expects 24 kHz PCM16 input, so this provider reports a
    24 kHz capture rate (the extension honors `sample_rate` from the session).
    """

    sample_rate = 24000
    _URL = "wss://api.openai.com/v1/realtime?intent=transcription"

    def __init__(
        self, api_key: str, model: str, language: str = "auto", vocabulary: list[str] | None = None
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._language = normalize_language(language)
        self._vocabulary = [redact(term) for term in (vocabulary or [])[:50]]
        self._ws = None
        self._interims: dict[str, str] = {}
        self._order: deque[str] = deque()
        self._finals: dict[str, TranscriptEvent] = {}
        self._buffered_bytes = 0
        self._pending_commits = 0
        self._has_audio = False
        self._finish_ack = asyncio.Event()
        self._finishing = False
        self._drained = asyncio.Event()
        self._drained.set()

    def _session_configuration(self) -> dict:
        transcription = {"model": self._model}
        # Omit the language hint for mixed-language input; never send "auto".
        if self._language != "auto":
            transcription["language"] = self._language
        else:
            transcription["prompt"] = (
                "Multilingual meeting. Speakers may switch languages within a sentence "
                "or between turns. Transcribe every spoken language in its original "
                "script. Do not translate."
            )
        base_prompt = (
            "Transcribe speech faithfully, preserving spoken details, numbers, "
            "names and corrections. Do not summarize or add words not heard. "
        )
        transcription["prompt"] = base_prompt + transcription.get("prompt", "")
        if self._vocabulary:
            transcription["prompt"] += " Expected names and technical terms: " + ", ".join(
                self._vocabulary
            )
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                **(
                    {"include": ["item.input_audio_transcription.logprobs"]}
                    if self._model.startswith("gpt-4o-transcribe") and "diarize" not in self._model
                    else {}
                ),
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": self.sample_rate},
                        "transcription": transcription,
                        "turn_detection": {
                            "type": "server_vad",
                            "prefix_padding_ms": 500,
                            "silence_duration_ms": 700,
                        },
                    }
                },
            },
        }

    async def connect(self) -> None:
        from websockets.asyncio.client import connect

        self._ws = await connect(
            self._URL,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            await self._ws.send(json.dumps(self._session_configuration()))
            # Don't report "connected" before the provider accepts configuration.
            async with asyncio.timeout(15):
                while True:
                    event = json.loads(await self._ws.recv())
                    if event.get("type") == "error":
                        raise RuntimeError("OpenAI rejected transcription session configuration")
                    if event.get("type") == "session.updated":
                        break
        except BaseException:
            await self.close()
            raise
        logger.info("live_stt.connected", provider="openai", model=self._model)

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is not None and chunk:
            self._has_audio = True
            self._buffered_bytes += len(chunk)
            payload = base64.b64encode(chunk).decode("ascii")
            await self._ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": payload}))

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        if self._ws is None:
            return
        async for message in self._ws:
            if isinstance(message, bytes):
                continue
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                continue
            kind = data.get("type", "")
            item_id = data.get("item_id", "")
            if kind == "input_audio_buffer.committed":
                self._buffered_bytes = 0
                self._pending_commits = max(0, self._pending_commits - 1)
                self._order.append(item_id)
                self._drained.clear()
            elif kind == "session.updated" and self._finishing:
                self._finish_ack.set()
            elif kind.endswith("input_audio_transcription.delta"):
                # Deltas are fragments, whereas the panel expects the full interim text.
                self._interims[item_id] = self._interims.get(item_id, "") + (
                    data.get("delta") or ""
                )
                if self._interims[item_id].strip():
                    yield TranscriptEvent(text=self._interims[item_id].strip(), is_final=False)
            elif kind.endswith("input_audio_transcription.completed"):
                self._interims.pop(item_id, None)
                text = (data.get("transcript") or "").strip()
                logprobs = [
                    entry.get("logprob")
                    for entry in (data.get("logprobs") or [])
                    if isinstance(entry, dict)
                    and isinstance(entry.get("logprob"), int | float)
                    and math.isfinite(entry["logprob"])
                ]
                confidence = (
                    math.exp(max(-100, min(0, sum(logprobs) / len(logprobs)))) if logprobs else None
                )
                self._finals[item_id] = TranscriptEvent(
                    text=text,
                    is_final=True,
                    confidence=confidence,
                    needs_review=(confidence is not None and confidence < 0.85)
                    or any(value < -2.3 for value in logprobs),
                )
                # Completion events may arrive out of order. Emit in audio commit order.
                ready = []
                while self._order and self._order[0] in self._finals:
                    segment = self._finals.pop(self._order.popleft())
                    if segment.text:
                        ready.append(segment)
                self._mark_drained()
                for segment in ready:
                    yield segment
            elif kind == "error":
                code = data.get("error", {}).get("code")
                if self._finishing and code == "input_audio_buffer_commit_empty":
                    # Server VAD may have committed the last turn just before finish().
                    self._pending_commits = max(0, self._pending_commits - 1)
                    self._mark_drained()
                    continue
                raise RuntimeError("OpenAI live transcription returned an error")
            elif kind.endswith("input_audio_transcription.failed"):
                raise RuntimeError("OpenAI could not transcribe an audio segment")

    def _mark_drained(self) -> None:
        if not self._order and not self._pending_commits:
            self._drained.set()

    async def finish(self) -> None:
        if self._ws is None:
            return
        self._finishing = True
        try:
            if self._has_audio or self._buffered_bytes:
                # A delayed VAD commit can reset the local byte counter after
                # later audio was sent. Always flush if any audio was sent.
                # Commits require at least 100 ms of PCM16 audio.
                minimum = self.sample_rate * 2 // 10
                if self._buffered_bytes < minimum:
                    await self.send_audio(bytes(minimum - self._buffered_bytes))
                self._pending_commits += 1
                self._drained.clear()
                await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                # Confirm the provider processed the commit request before we
                # judge the queue drained. A prior VAD turn finishing must not
                # close the socket before the final commit is acknowledged.
                await self._ws.send(json.dumps(self._session_configuration()))
                await asyncio.wait_for(self._finish_ack.wait(), timeout=15)
            await asyncio.wait_for(self._drained.wait(), timeout=15)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.close()
        finally:
            self._ws = None


def get_transcriber(
    language: str | None = None, *, vocabulary: list[str] | None = None
) -> StreamingTranscriber:
    """Construct a per-connection transcriber for the configured provider.

    Not cached — each live WebSocket needs its own STT session.
    """
    settings = get_settings()
    language = normalize_language(
        language if language is not None else settings.live_transcription_language
    )
    if settings.stt_provider == "deepgram":
        key = settings.deepgram_api_key.get_secret_value()
        if key:
            return DeepgramTranscriber(key, language, vocabulary)
        logger.warning("live_stt.fallback_to_null", reason="DEEPGRAM_API_KEY missing")
    elif settings.stt_provider == "openai":
        key = settings.openai_api_key.get_secret_value()
        if key:
            return OpenAIRealtimeTranscriber(
                key, settings.openai_realtime_model, language, vocabulary
            )
        logger.warning("live_stt.fallback_to_null", reason="OPENAI_API_KEY missing")
    return NullTranscriber()


def configured_sample_rate() -> int:
    """Capture sample rate for the configured provider (reported to the client)."""
    provider = get_settings().stt_provider
    if provider == "openai":
        return OpenAIRealtimeTranscriber.sample_rate
    return DeepgramTranscriber.sample_rate
