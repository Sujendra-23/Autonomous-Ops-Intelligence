"""Twilio Voice: inbound-call webhook and Media Streams WebSocket.

These routes are mounted at ``/voice/twilio`` (not ``/api``) on purpose: Twilio
cannot present our bearer/OIDC credentials, so `SecurityMiddleware` must not gate
them. They authenticate instead with Twilio's ``X-Twilio-Signature`` (webhook) and a
short-lived HMAC stream token minted by that webhook (WebSocket). In OIDC multi-tenant
mode voice runs against the base (deployment-level) settings, not a workspace's.

Flow: ``POST /incoming`` -> TwiML greeting + ``<Connect><Stream>`` -> Twilio opens
``/stream`` and sends 8 kHz mu-law frames -> STT relay -> Claude agent -> Twilio ``<Say>``
reply -> Google Calendar booking.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect

from app.config import Settings, base_settings
from app.integrations.calendar import GoogleCalendar
from app.integrations.twilio_voice import (
    CallResponder,
    TwilioCallResponder,
    form_pairs,
    say_and_hangup_twiml,
    say_and_stream_twiml,
    stream_url,
    verify_signature,
    verify_stream_token,
)
from app.logging import get_logger
from app.services.live_stt import StreamingTranscriber, get_transcriber
from app.services.voice_agent import GREETING, ClaudeVoiceLLM, VoiceAgent, VoiceAgentError
from app.services.voice_call import VoiceCall, VoiceSessions

logger = get_logger("app.api.voice")
router = APIRouter()

_sessions = VoiceSessions()


# Provider seams: tests override these with fakes via ``app.dependency_overrides``.
def get_settings_dep() -> Settings:
    return base_settings()


def get_voice_sessions() -> VoiceSessions:
    return _sessions


# The stream's collaborators are injected as factories so nothing (STT session, Claude
# client, Twilio client) is built until the connection's token has been verified.
def get_voice_transcriber() -> Callable[[], StreamingTranscriber]:
    return get_transcriber


def get_voice_agent() -> Callable[[], VoiceAgent]:
    return lambda: VoiceAgent(ClaudeVoiceLLM(), GoogleCalendar())


def get_voice_responder() -> Callable[[], CallResponder]:
    return TwilioCallResponder.from_settings


def _xml(body: str, status_code: int = 200) -> Response:
    return Response(body, status_code=status_code, media_type="application/xml")


@router.post("/incoming", summary="Twilio inbound-call webhook")
async def incoming_call(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    sessions: VoiceSessions = Depends(get_voice_sessions),
) -> Response:
    token = settings.twilio_auth_token.get_secret_value()
    if not (token and settings.twilio_public_base_url):
        return Response("Voice calls are not configured", status_code=503)

    form = await request.form()
    pairs = form_pairs(form.multi_items())
    # Twilio signs the exact URL it requested, so rebuild it from configuration rather
    # than from request headers a proxy may have rewritten.
    url = settings.twilio_public_base_url.rstrip("/") + "/voice/twilio/incoming"
    if request.url.query:
        url += "?" + request.url.query
    if not verify_signature(token, url, pairs, request.headers.get("x-twilio-signature")):
        logger.warning("voice.bad_signature")
        return Response("Invalid signature", status_code=403)

    fields = dict(pairs)
    call_sid = fields.get("CallSid", "")
    if not call_sid:
        return Response("Missing CallSid", status_code=400)

    if not settings.voice_enabled or not settings.stt_enabled:
        return _xml(
            say_and_hangup_twiml("Sorry, our phone assistant is unavailable. Please call later.")
        )

    sessions.get_or_create(call_sid, caller=fields.get("From", ""))
    greeting = GREETING.format(business=settings.voice_business_name)
    return _xml(
        say_and_stream_twiml(
            greeting, stream_url(settings.twilio_public_base_url, token, call_sid), call_sid
        )
    )


@router.websocket("/stream")
async def media_stream(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings_dep),
    sessions: VoiceSessions = Depends(get_voice_sessions),
    transcriber_factory: Callable[[], StreamingTranscriber] = Depends(get_voice_transcriber),
    agent_factory: Callable[[], VoiceAgent] = Depends(get_voice_agent),
    responder_factory: Callable[[], CallResponder] = Depends(get_voice_responder),
) -> None:
    token = settings.twilio_auth_token.get_secret_value()
    token_call_sid = verify_stream_token(token, websocket.query_params.get("token", ""))
    if token_call_sid is None:
        await websocket.close(code=1008)
        return
    try:
        transcriber, agent, responder = transcriber_factory(), agent_factory(), responder_factory()
    except VoiceAgentError:
        logger.warning("voice.not_configured")
        await websocket.close(code=1011)
        return
    await websocket.accept()

    call: VoiceCall | None = None
    consumer: asyncio.Task | None = None
    try:
        async with transcriber:
            call_sid_seen = False
            while True:
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                event = frame.get("event")
                if event == "start":
                    start = frame.get("start", {})
                    # The token is bound to one call; a stream claiming another is rejected.
                    if start.get("callSid") != token_call_sid:
                        await websocket.close(code=1008)
                        return
                    state = sessions.get_or_create(token_call_sid)
                    call = VoiceCall(
                        state=state, transcriber=transcriber, agent=agent, responder=responder
                    )
                    consumer = asyncio.create_task(call.consume())
                    call_sid_seen = True
                elif event == "media" and call is not None:
                    await call.on_media(frame.get("media", {}).get("payload", ""))
                elif event == "stop":
                    break
            if not call_sid_seen:
                return
            await transcriber.finish()
            if consumer is not None:
                await consumer
    except VoiceAgentError:
        logger.warning("voice.agent_error")
    except Exception:
        logger.exception("voice.stream_error", call=token_call_sid)
    finally:
        if consumer is not None and not consumer.done():
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await consumer
        if call is not None and call.state.status != "collecting":
            sessions.discard(token_call_sid)
        with contextlib.suppress(Exception):
            await websocket.close()
