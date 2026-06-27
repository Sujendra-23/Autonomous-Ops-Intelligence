"""Streaming speech-to-text for the live note-taker.

The browser extension captures the meeting tab's audio and streams 16 kHz mono
PCM16 frames to our WebSocket; we relay those frames to a streaming STT provider
and surface interim/final transcripts back. Keeping the relay server-side means
the **provider API key never leaves the backend** — it stays a `SecretStr`,
consistent with the rest of the project's secret handling.

Providers sit behind `StreamingTranscriber` so the live API doesn't care which
one is wired. Deepgram is implemented (a stable, low-latency WebSocket STT);
`NullTranscriber` is the no-key fallback so the endpoint still runs in dev (it
just produces no text). Add OpenAI Realtime / AssemblyAI as further subclasses.
"""

from __future__ import annotations

import base64
import contextlib
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.config import get_settings
from app.logging import get_logger

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

    _URL = (
        "wss://api.deepgram.com/v1/listen"
        f"?encoding={ENCODING}&sample_rate={SAMPLE_RATE}&channels={CHANNELS}"
        "&model=nova-2&interim_results=true&smart_format=true&punctuate=true"
    )

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._ws = None

    async def connect(self) -> None:
        # Imported lazily so the module (and its tests) load without the optional
        # `websockets` dependency present.
        from websockets.asyncio.client import connect

        self._ws = await connect(self._URL, subprotocols=["token", self._api_key])
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
            channel = data.get("channel")
            if not channel:
                continue
            alts = channel.get("alternatives") or []
            text = (alts[0].get("transcript") if alts else "") or ""
            if text.strip():
                yield TranscriptEvent(text=text.strip(), is_final=bool(data.get("is_final")))

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

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._ws = None

    async def connect(self) -> None:
        from websockets.asyncio.client import connect

        self._ws = await connect(
            self._URL,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        )
        await self._ws.send(
            json.dumps(
                {
                    "type": "transcription_session.update",
                    "session": {
                        "input_audio_format": "pcm16",
                        "input_audio_transcription": {"model": self._model},
                        "turn_detection": {"type": "server_vad"},
                    },
                }
            )
        )
        logger.info("live_stt.connected", provider="openai", model=self._model)

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is not None and chunk:
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
            if kind.endswith("input_audio_transcription.delta"):
                text = (data.get("delta") or "").strip()
                if text:
                    yield TranscriptEvent(text=text, is_final=False)
            elif kind.endswith("input_audio_transcription.completed"):
                text = (data.get("transcript") or "").strip()
                if text:
                    yield TranscriptEvent(text=text, is_final=True)

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.close()
        finally:
            self._ws = None


def get_transcriber() -> StreamingTranscriber:
    """Construct a per-connection transcriber for the configured provider.

    Not cached — each live WebSocket needs its own STT session.
    """
    settings = get_settings()
    if settings.stt_provider == "deepgram":
        key = settings.deepgram_api_key.get_secret_value()
        if key:
            return DeepgramTranscriber(key)
        logger.warning("live_stt.fallback_to_null", reason="DEEPGRAM_API_KEY missing")
    elif settings.stt_provider == "openai":
        key = settings.openai_api_key.get_secret_value()
        if key:
            return OpenAIRealtimeTranscriber(key, settings.openai_realtime_model)
        logger.warning("live_stt.fallback_to_null", reason="OPENAI_API_KEY missing")
    return NullTranscriber()


def configured_sample_rate() -> int:
    """Capture sample rate for the configured provider (reported to the client)."""
    provider = get_settings().stt_provider
    if provider == "openai":
        return OpenAIRealtimeTranscriber.sample_rate
    return DeepgramTranscriber.sample_rate
