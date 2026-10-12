"""Twilio webhook + Media Streams WebSocket, end to end with every provider faked."""

import asyncio
import base64
import json
from array import array

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from app.api import voice
from app.config import Settings
from app.integrations.twilio_voice import compute_signature, make_stream_token
from app.services.audio import mulaw_encode, pcm16_bytes_to_samples
from app.services.live_stt import StreamingTranscriber, TranscriptEvent
from app.services.voice_agent import AgentOutcome
from app.services.voice_call import VoiceSessions

BASE = "https://voice.example.com"
TOKEN = "twilio-auth-token"
FORM = {"CallSid": "CA100", "From": "+15125550100", "To": "+15125550199"}


def make_settings(**overrides) -> Settings:
    values = {
        "twilio_auth_token": SecretStr(TOKEN),
        "twilio_public_base_url": BASE,
        "anthropic_api_key": SecretStr("sk-test"),
        "stt_provider": "deepgram",
        "deepgram_api_key": SecretStr("dg-test"),
        "voice_business_name": "Acme",
    }
    return Settings(_env_file=None, **{**values, **overrides})


class FakeTranscriber(StreamingTranscriber):
    """Records audio; emits the scripted caller utterances once audio has arrived."""

    sample_rate = 16000

    def __init__(self, finals=()):
        self.audio: list[bytes] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.finals = list(finals)
        self.connected = self.closed = False

    async def connect(self):
        self.connected = True

    async def send_audio(self, chunk):
        self.audio.append(chunk)
        if len(self.audio) == 1:  # first frame "wakes" the STT
            for text in self.finals:
                self.queue.put_nowait(TranscriptEvent(text=text, is_final=True))
            self.queue.put_nowait(TranscriptEvent(text="interim", is_final=False))

    async def events(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def finish(self):
        await self.close()

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)


class FakeAgent:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.heard: list[str] = []

    async def handle_utterance(self, state, text):
        self.heard.append(text)
        return self.outcomes.pop(0)


class FakeResponder:
    def __init__(self):
        self.replies = []

    async def reply(self, call_sid, text, *, reconnect):
        self.replies.append((call_sid, text, reconnect))


@pytest.fixture
def env():
    settings = make_settings()
    ns = type("Env", (), {})()
    ns.settings = settings
    ns.sessions = VoiceSessions()
    ns.transcriber = FakeTranscriber(["I need a plumber", "tomorrow at three"])
    ns.agent = FakeAgent([AgentOutcome(reply="What is your name?", done=False)])
    ns.responder = FakeResponder()
    app = FastAPI()
    app.include_router(voice.router, prefix="/voice/twilio")
    app.dependency_overrides.update(
        {
            voice.get_settings_dep: lambda: ns.settings,
            voice.get_voice_sessions: lambda: ns.sessions,
            voice.get_voice_transcriber: lambda: lambda: ns.transcriber,
            voice.get_voice_agent: lambda: lambda: ns.agent,
            voice.get_voice_responder: lambda: lambda: ns.responder,
        }
    )
    ns.client = TestClient(app)
    return ns


def signed_post(env, form=None, url=f"{BASE}/voice/twilio/incoming", token=TOKEN):
    form = FORM if form is None else form
    signature = compute_signature(token, url, list(form.items()))
    return env.client.post(
        "/voice/twilio/incoming", data=form, headers={"X-Twilio-Signature": signature}
    )


# ------------------------------- webhook ------------------------------------ #


def test_incoming_call_returns_greeting_and_stream_twiml(env):
    response = signed_post(env)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.text
    assert "<Say>Thanks for calling Acme." in body
    assert (
        "<Connect><Stream url=" in body
        and "wss://voice.example.com/voice/twilio/stream?token=" in body
    )
    assert (
        "CA100" in env.sessions._states and env.sessions._states["CA100"].caller == "+15125550100"
    )


@pytest.mark.parametrize("kind", ["missing", "wrong-token", "wrong-url", "tampered-form"])
def test_incoming_call_rejects_bad_signature(env, kind):
    if kind == "missing":
        response = env.client.post("/voice/twilio/incoming", data=FORM)
    elif kind == "wrong-token":
        response = signed_post(env, token="attacker")
    elif kind == "wrong-url":
        response = signed_post(env, url="https://evil.example.com/voice/twilio/incoming")
    else:
        signature = compute_signature(TOKEN, f"{BASE}/voice/twilio/incoming", list(FORM.items()))
        response = env.client.post(
            "/voice/twilio/incoming",
            data={**FORM, "From": "+19998887777"},
            headers={"X-Twilio-Signature": signature},
        )
    assert response.status_code == 403
    assert "CA100" not in env.sessions._states


def test_incoming_call_signature_covers_query_string(env):
    url = f"{BASE}/voice/twilio/incoming?x=1"
    signature = compute_signature(TOKEN, url, list(FORM.items()))
    ok = env.client.post(
        "/voice/twilio/incoming?x=1", data=FORM, headers={"X-Twilio-Signature": signature}
    )
    assert ok.status_code == 200


def test_incoming_call_requires_configuration(env):
    env.settings.twilio_auth_token = SecretStr("")
    assert signed_post(env).status_code == 503


def test_incoming_call_without_stt_says_unavailable_and_hangs_up(env):
    env.settings.stt_provider = "none"
    response = signed_post(env)
    assert response.status_code == 200
    assert "unavailable" in response.text and "<Hangup/>" in response.text
    assert "<Stream" not in response.text


def test_incoming_call_requires_call_sid(env):
    assert signed_post(env, form={"From": "+1"}).status_code == 400


def test_voice_routes_are_registered_on_the_app_outside_the_api_prefix():
    from app.main import app

    client = TestClient(app)
    # Unconfigured deployment: webhook answers 503 (not 404/401), and the stream refuses.
    assert client.post("/voice/twilio/incoming", data=FORM).status_code == 503
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/voice/twilio/stream"):
        pass


# ------------------------------- websocket ---------------------------------- #


def frame(event, **fields):
    return json.dumps({"event": event, **fields})


def media(samples):
    return frame("media", media={"payload": base64.b64encode(mulaw_encode(samples)).decode()})


def stream_path(call_sid="CA100", token=None):
    token = token or make_stream_token(TOKEN, call_sid)
    return f"/voice/twilio/stream?token={token}"


def test_stream_rejects_missing_or_forged_token(env):
    for path in (
        "/voice/twilio/stream",
        "/voice/twilio/stream?token=junk",
        stream_path(token=make_stream_token("attacker", "CA100")),
    ):
        with pytest.raises(WebSocketDisconnect), env.client.websocket_connect(path):
            pass
    assert not env.transcriber.connected


def test_stream_rejects_start_for_a_different_call(env):
    env.sessions.get_or_create("CA100")
    with env.client.websocket_connect(stream_path("CA100")) as ws:
        ws.send_text(frame("connected"))
        ws.send_text(frame("start", start={"callSid": "CA999", "streamSid": "MZ1"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert env.agent.heard == []


def test_stream_relays_audio_and_replies_to_caller(env):
    env.sessions.get_or_create("CA100", caller="+15125550100")
    with env.client.websocket_connect(stream_path()) as ws:
        ws.send_text(frame("connected", protocol="Call", version="1.0.0"))
        ws.send_text(frame("start", start={"callSid": "CA100", "streamSid": "MZ1"}))
        ws.send_text(media(array("h", [0] * 160)))
        ws.send_text(media(array("h", [0] * 160)))
        ws.send_text(frame("media", media={"payload": "!!not-base64!!"}))  # ignored, not fatal
        ws.send_text(frame("stop"))
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_text()
    # 8 kHz mu-law -> 16 kHz PCM16: 160 samples become ~320 samples (640 bytes).
    assert [len(chunk) for chunk in env.transcriber.audio] == [636, 640]
    assert not any(pcm16_bytes_to_samples(env.transcriber.audio[0]))
    assert env.transcriber.connected and env.transcriber.closed
    # Two final segments inside the utterance gap are merged into one caller turn;
    # interim results are never sent to the agent.
    assert env.agent.heard == ["I need a plumber tomorrow at three"]
    assert env.responder.replies == [("CA100", "What is your name?", True)]


def test_finished_call_replies_without_reconnect_and_discards_state(env):
    env.agent = FakeAgent([AgentOutcome(reply="Booked. Goodbye.", done=True, booked=True)])
    state = env.sessions.get_or_create("CA100")
    state.status = "booked"
    with env.client.websocket_connect(stream_path()) as ws:
        ws.send_text(frame("start", start={"callSid": "CA100"}))
        ws.send_text(media(array("h", [0] * 160)))
        ws.send_text(frame("stop"))
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_text()
    assert env.responder.replies == [("CA100", "Booked. Goodbye.", False)]
    assert "CA100" not in env.sessions._states


def test_responder_failure_does_not_crash_the_stream(env):
    class Broken(FakeResponder):
        async def reply(self, *a, **k):
            raise RuntimeError("twilio down")

    env.responder = Broken()
    env.sessions.get_or_create("CA100")
    with env.client.websocket_connect(stream_path()) as ws:
        ws.send_text(frame("start", start={"callSid": "CA100"}))
        ws.send_text(media(array("h", [0] * 160)))
        ws.send_text(frame("stop"))
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_text()
    assert env.agent.heard  # the turn was processed; delivery failure was logged only


def test_stream_survives_malformed_frames(env):
    env.transcriber = FakeTranscriber([])
    env.sessions.get_or_create("CA100")
    with env.client.websocket_connect(stream_path()) as ws:
        ws.send_text("not json")
        ws.send_text(frame("media", media={"payload": "AAAA"}))  # media before start: ignored
        ws.send_text(frame("start", start={"callSid": "CA100"}))
        ws.send_text(frame("stop"))
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_text()
    assert env.transcriber.audio == []


def test_stt_provider_default_stays_none():
    assert Settings(_env_file=None).stt_provider == "none"
    assert not Settings(_env_file=None).voice_enabled
