"""Quality cues and end-of-stream guarantees; provider calls are simulated."""

import asyncio
import json
import math
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.live import LiveSessionCreate
from app.models.transcript import Transcript
from app.services.dlp import protect_transcript
from app.services.live_stt import DeepgramTranscriber, OpenAIRealtimeTranscriber


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, data):
        event = json.loads(data)
        self.sent.append(event)
        if event.get("type") == "CloseStream":
            self.queue.put_nowait(
                json.dumps(
                    {
                        "type": "Results",
                        "is_final": True,
                        "channel": {
                            "alternatives": [
                                {
                                    "transcript": "The very last word.",
                                    "confidence": 0.92,
                                    "words": [{"word": "word", "confidence": 0.4}],
                                }
                            ]
                        },
                    }
                )
            )
            self.queue.put_nowait(json.dumps({"type": "Metadata"}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)


async def test_deepgram_waits_for_last_words_before_closing(monkeypatch):
    socket = Socket()
    connect = AsyncMock(return_value=socket)
    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    client = DeepgramTranscriber("private", "en", ["Kubernetes", "Priya"])
    await client.connect()
    query = parse_qs(urlparse(connect.call_args.args[0]).query)
    assert query["keyterm"] == ["Kubernetes", "Priya"]
    assert query["filler_words"] == ["true"]
    segments = []

    async def consume():
        async for event in client.events():
            segments.append(event)

    task = asyncio.create_task(consume())
    await client.finish()
    await task
    assert segments[0].text == "The very last word."
    assert segments[0].confidence == 0.92 and segments[0].needs_review
    assert socket.closed


async def test_openai_prompt_and_confidence_follow_commit_order():
    client = OpenAIRealtimeTranscriber("private", "gpt-4o-transcribe", "en", ["Kubernetes"])
    config = client._session_configuration()
    assert "Kubernetes" in config["session"]["audio"]["input"]["transcription"]["prompt"]
    assert config["session"]["include"] == ["item.input_audio_transcription.logprobs"]
    assert config["session"]["audio"]["input"]["turn_detection"]["prefix_padding_ms"] == 500
    socket = Socket()
    client._ws = socket
    for event in [
        {"type": "input_audio_buffer.committed", "item_id": "first"},
        {"type": "input_audio_buffer.committed", "item_id": "second"},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "second",
            "transcript": "Uncertain name.",
            "logprobs": [{"logprob": -2}],
        },
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "first",
            "transcript": "Clear introduction.",
            "logprobs": [{"logprob": -0.01}],
        },
    ]:
        socket.queue.put_nowait(json.dumps(event))
    socket.queue.put_nowait(None)
    segments = [event async for event in client.events()]
    assert [event.text for event in segments] == ["Clear introduction.", "Uncertain name."]
    assert not segments[0].needs_review and segments[1].needs_review
    assert segments[1].confidence == pytest.approx(math.exp(-2))


def test_transcript_quality_metadata_obeys_existing_redaction_policy():
    transcript = Transcript(
        title="Planning",
        content="Contact alice@example.com",
        sensitivity_label="internal",
        transcription_segments=[
            {"text": "Contact alice@example.com", "confidence": 0.4, "needs_review": True}
        ],
    )
    protect_transcript(transcript)
    assert "alice@example.com" not in transcript.content
    assert "alice@example.com" not in transcript.transcription_segments[0]["text"]
    assert transcript.transcription_segments[0]["confidence"] == 0.4


def test_desktop_source_and_vocabulary_are_validated():
    request = LiveSessionCreate(source="desktop", capture_app="teams", vocabulary=["Kubernetes"])
    assert request.source == "desktop" and request.vocabulary == ["Kubernetes"]
    assert LiveSessionCreate().source == "live"
    with pytest.raises(ValueError):
        LiveSessionCreate(vocabulary=["x"] * 51)


async def test_desktop_reconnect_waits_for_previous_writer_to_finish():
    import uuid

    from app.api.live import desktop_capture_lock

    identifier = uuid.uuid4()
    async with desktop_capture_lock(identifier, True) as first:
        assert first
        async with desktop_capture_lock(identifier, True) as overlapping:
            assert overlapping is False
    async with desktop_capture_lock(identifier, True) as after_final_words:
        assert after_final_words
