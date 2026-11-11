"""Realtime protocol tests; no credentials or network calls required."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services.live_stt import OpenAIRealtimeTranscriber


class FakeSocket:
    def __init__(self, events=()):
        self.sent = []
        self.queue = asyncio.Queue()
        for event in events:
            self.queue.put_nowait(json.dumps(event))
        self.closed = False
        self.final_on_commit = False

    async def send(self, message):
        event = json.loads(message)
        self.sent.append(event)
        if event["type"] == "session.update":
            self.queue.put_nowait(json.dumps({"type": "session.updated"}))
        if self.final_on_commit and event["type"] == "input_audio_buffer.commit":
            self.queue.put_nowait(
                json.dumps({"type": "input_audio_buffer.committed", "item_id": "last"})
            )
            self.queue.put_nowait(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "last",
                        "transcript": "Last sentence.",
                    }
                )
            )

    async def recv(self):
        return await self.queue.get()

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.queue.get()
        if message is None:
            raise StopAsyncIteration
        return message


async def test_connect_waits_for_ga_session_ack(monkeypatch):
    socket = FakeSocket([{"type": "session.created"}, {"type": "session.updated"}])
    connect = AsyncMock(return_value=socket)
    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    await transcriber.connect()
    assert connect.call_args.kwargs["additional_headers"] == {"Authorization": "Bearer test-key"}
    message = socket.sent[0]
    assert message["type"] == "session.update"
    assert message["session"]["type"] == "transcription"
    assert message["session"]["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    await transcriber.close()


async def test_connect_rejection_closes_socket_without_logging_secret(monkeypatch):
    socket = FakeSocket([{"type": "error", "error": {"message": "private response"}}])
    monkeypatch.setattr("websockets.asyncio.client.connect", AsyncMock(return_value=socket))
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    with pytest.raises(RuntimeError, match="rejected transcription session"):
        await transcriber.connect()
    assert socket.closed


async def test_interims_accumulate_and_finals_keep_audio_order():
    def transcript(kind, item, **data):
        return {
            "type": f"conversation.item.input_audio_transcription.{kind}",
            "item_id": item,
            **data,
        }

    socket = FakeSocket(
        [
            {"type": "input_audio_buffer.committed", "item_id": "one"},
            {"type": "input_audio_buffer.committed", "item_id": "two"},
            transcript("delta", "one", delta="Hello"),
            transcript("delta", "one", delta=" world"),
            transcript("completed", "two", transcript="Second sentence."),
            transcript("completed", "one", transcript="Hello world."),
        ]
    )
    socket.queue.put_nowait(None)
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    transcriber._ws = socket
    events = [event async for event in transcriber.events()]
    assert [e.text for e in events if not e.is_final] == ["Hello", "Hello world"]
    assert [e.text for e in events if e.is_final] == ["Hello world.", "Second sentence."]
    assert transcriber._drained.is_set()


async def test_finish_flushes_short_last_chunk_before_closing():
    socket = FakeSocket()
    socket.final_on_commit = True
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    transcriber._ws = socket
    finals = []

    async def consume():
        async for event in transcriber.events():
            if event.is_final:
                finals.append(event.text)

    consumer = asyncio.create_task(consume())
    await transcriber.send_audio(bytes(100))
    await transcriber.finish()
    await consumer
    assert finals == ["Last sentence."]
    assert socket.closed
    assert [e["type"] for e in socket.sent] == [
        "input_audio_buffer.append",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "session.update",
    ]


async def test_finish_handles_server_vad_commit_race():
    socket = FakeSocket([{"type": "error", "error": {"code": "input_audio_buffer_commit_empty"}}])
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    transcriber._ws = socket
    transcriber._buffered_bytes = 4800

    async def consume():
        return [event async for event in transcriber.events()]

    consumer = asyncio.create_task(consume())
    await transcriber.finish()
    assert await consumer == []
    assert socket.closed


async def test_transcription_failure_is_not_silent():
    socket = FakeSocket([{"type": "conversation.item.input_audio_transcription.failed"}])
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    transcriber._ws = socket
    with pytest.raises(RuntimeError, match="could not transcribe"):
        _ = [event async for event in transcriber.events()]


async def test_live_session_records_date_for_relative_deadlines(monkeypatch):
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from app.api import live

    monkeypatch.setattr(live, "meeting_context", AsyncMock(return_value={}))
    monkeypatch.setattr(live, "resolve_meeting_project", AsyncMock(return_value=None))
    session = MagicMock(commit=AsyncMock())

    async def refresh(row):
        row.id = uuid.uuid4()

    session.refresh = AsyncMock(side_effect=refresh)
    before = datetime.now(UTC)
    await live.create_live_session(live.LiveSessionCreate(title="Live test"), session)
    row = session.add.call_args.args[0]
    assert before <= row.meeting_date <= datetime.now(UTC)


def test_relative_date_prompt_has_correct_calendar_reference():
    from app.llm.prompts import build_user_prompt

    prompt = build_user_prompt(
        "Alex will finish by Friday.", meeting_date="2026-10-04T09:00:00-07:00"
    )
    assert "Sunday=2026-10-04" in prompt
    assert "Friday=2026-10-09" in prompt
    assert "Leave relative deadlines such as 'Friday' null" in build_user_prompt(
        "Finish by Friday."
    )


@pytest.mark.parametrize("language", ["auto", "ne", "hi", "es", "ar", "ta"])
async def test_openai_multilingual_config_uses_optional_language_hint(monkeypatch, language):
    socket = FakeSocket([{"type": "session.updated"}])
    monkeypatch.setattr("websockets.asyncio.client.connect", AsyncMock(return_value=socket))
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe", language)
    await transcriber.connect()
    settings = socket.sent[0]["session"]["audio"]["input"]["transcription"]
    assert settings["model"] == "gpt-4o-transcribe"
    if language == "auto":
        assert "language" not in settings and "languages" not in settings
        assert "Multilingual meeting" in settings["prompt"]
        assert "Do not translate" in settings["prompt"]
    else:
        assert settings["language"] == language
    await transcriber.close()


@pytest.mark.parametrize(
    "language, expected", [("auto", "multi"), ("ne", "ne"), ("hi", "hi"), ("es", "es")]
)
async def test_deepgram_uses_multilingual_nova3_without_english_default(
    monkeypatch, language, expected
):
    from urllib.parse import parse_qs, urlsplit

    from app.services.live_stt import DeepgramTranscriber

    socket = FakeSocket()
    connect = AsyncMock(return_value=socket)
    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    transcriber = DeepgramTranscriber("private-test-key", language)
    await transcriber.connect()
    url = connect.call_args.args[0]
    query = parse_qs(urlsplit(url).query)
    assert query["model"] == ["nova-3"]
    assert query["language"] == [expected]
    assert query["sample_rate"] == ["16000"]
    assert "private-test-key" not in url
    await transcriber.close()


async def test_mixed_script_transcripts_are_preserved_without_translation():
    texts = [
        "नमस्ते team, म भोलि रिपोर्ट पठाउँछु।",
        "कल हम review करेंगे।",
        "سنراجع الخطة غدًا.",
        "Revisamos el plan tomorrow.",
    ]
    events = []
    for index, text in enumerate(texts):
        events.extend(
            [
                {"type": "input_audio_buffer.committed", "item_id": str(index)},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": str(index),
                    "transcript": text,
                },
            ]
        )
    socket = FakeSocket(events)
    socket.queue.put_nowait(None)
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    transcriber._ws = socket
    assert [event.text async for event in transcriber.events()] == texts


def test_language_preferences_are_validated_and_connection_local(monkeypatch):
    from types import SimpleNamespace

    from pydantic import SecretStr, ValidationError

    from app.api.live import LiveSessionCreate
    from app.services import live_stt

    monkeypatch.setattr(
        live_stt,
        "get_settings",
        lambda: SimpleNamespace(
            stt_provider="openai",
            live_transcription_language="ne",
            openai_api_key=SecretStr("test-key"),
            openai_realtime_model="gpt-4o-transcribe",
        ),
    )
    a, b = live_stt.get_transcriber(), live_stt.get_transcriber("es")
    assert a is not b and a._language == "ne" and b._language == "es"
    assert live_stt.get_transcriber("auto")._language == "auto"
    assert LiveSessionCreate(language=" NE ").language == "ne"
    for invalid in ("unknown", "auto&model=other", "en\r\nX-Header: bad"):
        with pytest.raises(ValidationError):
            LiveSessionCreate(language=invalid)


async def test_live_session_persists_listening_preference_for_reconnect(monkeypatch):
    import uuid
    from unittest.mock import MagicMock

    from app.api import live

    monkeypatch.setattr(live, "meeting_context", AsyncMock(return_value={}))
    monkeypatch.setattr(live, "resolve_meeting_project", AsyncMock(return_value=None))
    session = MagicMock(commit=AsyncMock())

    async def refresh(row):
        row.id = uuid.uuid4()

    session.refresh = AsyncMock(side_effect=refresh)
    result = await live.create_live_session(
        live.LiveSessionCreate(title="बहुभाषी बैठक", language="ne"), session
    )
    saved = session.add.call_args.args[0]
    assert saved.transcription_language == "ne"
    assert result.language == "ne"
    assert saved.title == "बहुभाषी बैठक"


async def test_delayed_vad_commit_does_not_drop_audio_sent_after_that_turn():
    socket = FakeSocket(
        [
            {"type": "input_audio_buffer.committed", "item_id": "earlier"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "earlier",
                "transcript": "Hello team.",
            },
        ]
    )
    socket.final_on_commit = True
    transcriber = OpenAIRealtimeTranscriber("test-key", "gpt-4o-transcribe")
    transcriber._ws = socket
    await transcriber.send_audio(bytes(4800))
    finals = []

    async def consume():
        async for event in transcriber.events():
            if event.is_final:
                finals.append(event.text)

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    assert transcriber._buffered_bytes == 0
    await transcriber.finish()
    await consumer
    assert finals == ["Hello team.", "Last sentence."]
    assert socket.closed
