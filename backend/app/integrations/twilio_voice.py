"""Twilio Voice helpers: webhook signature check, TwiML, stream tokens, call updates.

Everything here is pure or takes an injectable HTTP transport so it can be unit
tested without Twilio. Credentials are `SecretStr` in settings and only unwrapped
at the point of use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Mapping, Sequence
from typing import Protocol
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("app.integrations.twilio_voice")

STREAM_TOKEN_TTL_SECONDS = 3600


# --------------------------------------------------------------------------- #
# Webhook signature                                                            #
# --------------------------------------------------------------------------- #


def compute_signature(auth_token: str, url: str, params: Sequence[tuple[str, str]]) -> str:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(token, url + sorted key+value pairs))."""
    payload = url + "".join(f"{key}{value}" for key, value in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    auth_token: str, url: str, params: Sequence[tuple[str, str]], signature: str | None
) -> bool:
    if not auth_token or not signature:
        return False
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)


# --------------------------------------------------------------------------- #
# Stream tokens                                                                #
# --------------------------------------------------------------------------- #
# The Media Streams WebSocket cannot carry our API credentials, so the incoming
# webhook (already signature-verified) mints a short-lived token bound to the
# call, and the WebSocket must present it. This avoids depending on how Twilio
# signs the WebSocket handshake.


def _token_mac(auth_token: str, call_sid: str, expires: int) -> str:
    message = f"voice-stream:{call_sid}:{expires}".encode()
    return hmac.new(auth_token.encode(), message, hashlib.sha256).hexdigest()


def make_stream_token(auth_token: str, call_sid: str, *, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + STREAM_TOKEN_TTL_SECONDS)
    return f"{call_sid}.{expires}.{_token_mac(auth_token, call_sid, expires)}"


def verify_stream_token(auth_token: str, token: str, *, now: float | None = None) -> str | None:
    """Return the call SID the token is bound to, or None if invalid/expired."""
    if not auth_token:
        return None
    try:
        call_sid, expires_raw, mac = token.rsplit(".", 2)
        expires = int(expires_raw)
    except ValueError:
        return None
    if expires < (time.time() if now is None else now):
        return None
    if not hmac.compare_digest(_token_mac(auth_token, call_sid, expires), mac):
        return None
    return call_sid


def stream_url(public_base_url: str, auth_token: str, call_sid: str) -> str:
    base = public_base_url.rstrip("/")
    ws_base = "wss://" + base.split("://", 1)[-1]
    query = urlencode({"token": make_stream_token(auth_token, call_sid)}, quote_via=quote)
    return f"{ws_base}/voice/twilio/stream?{query}"


# --------------------------------------------------------------------------- #
# TwiML                                                                        #
# --------------------------------------------------------------------------- #


def _stream_xml(url: str, call_sid: str) -> str:
    return (
        f"<Connect><Stream url={quoteattr(url)}>"
        f'<Parameter name="callSid" value={quoteattr(call_sid)}/>'
        "</Stream></Connect>"
    )


def say_and_stream_twiml(text: str, url: str, call_sid: str) -> str:
    return f"<Response><Say>{escape(text)}</Say>{_stream_xml(url, call_sid)}</Response>"


def say_and_hangup_twiml(text: str) -> str:
    return f"<Response><Say>{escape(text)}</Say><Hangup/></Response>"


# --------------------------------------------------------------------------- #
# Speaking to the caller mid-call                                              #
# --------------------------------------------------------------------------- #


class CallResponder(Protocol):
    async def reply(self, call_sid: str, text: str, *, reconnect: bool) -> None: ...


class TwilioCallResponder:
    """Speak to the caller by replacing the live call's TwiML via the REST API.

    The agent has no TTS of its own: Twilio's `<Say>` voices the reply, then (unless
    the call is ending) `<Connect><Stream>` re-opens the media stream so the caller's
    next utterance is transcribed. Updating the call closes the current stream.
    """

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        public_base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._public_base_url = public_base_url
        self._transport = transport

    @classmethod
    def from_settings(cls) -> TwilioCallResponder:
        s = get_settings()
        return cls(
            account_sid=s.twilio_account_sid,
            auth_token=s.twilio_auth_token.get_secret_value(),
            public_base_url=s.twilio_public_base_url,
        )

    async def reply(self, call_sid: str, text: str, *, reconnect: bool) -> None:
        if reconnect:
            twiml = say_and_stream_twiml(
                text, stream_url(self._public_base_url, self._auth_token, call_sid), call_sid
            )
        else:
            twiml = say_and_hangup_twiml(text)
        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/{quote(self._account_sid, safe='')}"
            f"/Calls/{quote(call_sid, safe='')}.json"
        )
        async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
            response = await client.post(
                url, data={"Twiml": twiml}, auth=(self._account_sid, self._auth_token)
            )
        if response.status_code >= 400:
            # Never log the body: it can echo the account SID and call details.
            logger.warning("twilio.call_update_failed", status=response.status_code)
            raise RuntimeError(f"Twilio call update failed with HTTP {response.status_code}")


def form_pairs(items: Mapping | Sequence) -> list[tuple[str, str]]:
    pairs = items.items() if isinstance(items, Mapping) else items
    return [(str(k), str(v)) for k, v in pairs]
