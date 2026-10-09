"""Claude voice agent: turn a phone transcript into a booked calendar appointment.

The caller's finalized utterances come from the STT relay. For each one, the LLM
(`VoiceLLM`) extracts name / need / requested time and drafts the next spoken
reply; this module owns everything that must be deterministic: merging slots,
validating the time, checking the calendar for conflicts, creating the event
idempotently, and bounding the number of turns. The LLM and the calendar are
injected so the whole flow is unit-testable without a network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anthropic
from fastapi import HTTPException

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("app.services.voice_agent")

GREETING = (
    "Thanks for calling {business}. Please tell me your name, "
    "what you need help with, and when you would like us to come."
)


class VoiceAgentError(RuntimeError):
    """The LLM call failed or returned something unusable."""


@dataclass
class CallState:
    call_sid: str
    caller: str = ""
    name: str | None = None
    need: str | None = None
    start: datetime | None = None
    turns: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    status: Literal["collecting", "booked", "handoff"] = "collecting"
    event_id: str | None = None
    created_at: float = 0.0


@dataclass
class AgentDecision:
    reply: str
    name: str | None = None
    need: str | None = None
    requested_start: str | None = None  # ISO 8601, business-local if no offset
    ready_to_book: bool = False


@dataclass
class AgentOutcome:
    reply: str
    done: bool
    booked: bool = False


class VoiceLLM(Protocol):
    async def decide(
        self, state: CallState, *, now: datetime, timezone: str, business: str
    ) -> AgentDecision: ...


class CalendarPort(Protocol):
    async def list_events(self, start: datetime, end: datetime, page_token: str | None = None): ...

    async def create_event(self, **kwargs): ...


# --------------------------------------------------------------------------- #
# Claude                                                                       #
# --------------------------------------------------------------------------- #

_TOOL = {
    "name": "record_turn",
    "description": "Record what the caller has told you so far and what to say next.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Caller's name, only if stated."},
            "need": {"type": "string", "description": "What service/problem they need."},
            "requested_start": {
                "type": "string",
                "description": "Requested appointment start, ISO 8601 with UTC offset.",
            },
            "ready_to_book": {
                "type": "boolean",
                "description": (
                    "True only after you read back name, need and time and the caller agreed."
                ),
            },
            "reply": {
                "type": "string",
                "description": "What to say to the caller next: one or two short sentences.",
            },
        },
        "required": ["reply", "ready_to_book"],
    },
}

_SYSTEM = """You are the phone scheduling assistant for {business}, a home-service contractor.
You are talking to a caller over the phone; the text you receive is an automatic transcript and
may contain recognition errors. You have already greeted them and asked for their name, what they
need, and when they would like an appointment.

Collect exactly three things: the caller's name, what they need, and a start time. Ask for whatever
is missing, one question at a time, in at most two short sentences (your reply is spoken aloud;
no lists, no markdown). Resolve relative times ("tomorrow at 3") against the current time below and
express them with a UTC offset in `requested_start`. Once you have all three, read them back and
ask the caller to confirm; set `ready_to_book` true only after they agree. Never promise anything
beyond booking the appointment, and never invent details. Always answer by calling record_turn.

Current time: {now} ({timezone}). Business timezone: {timezone}."""


class ClaudeVoiceLLM:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None, model: str = "") -> None:
        settings = get_settings()
        key = settings.anthropic_api_key.get_secret_value()
        if client is None:
            if not key:
                raise VoiceAgentError("ANTHROPIC_API_KEY is not configured")
            client = anthropic.AsyncAnthropic(api_key=key)
        self._client = client
        self._model = model or settings.voice_agent_model or settings.anthropic_model

    async def decide(
        self, state: CallState, *, now: datetime, timezone: str, business: str
    ) -> AgentDecision:
        known = {
            "name": state.name,
            "need": state.need,
            "start": state.start.isoformat() if state.start else None,
        }
        system = (
            _SYSTEM.format(
                business=business, now=now.isoformat(timespec="minutes"), timezone=timezone
            )
            + f"\nAlready collected: {known}"
        )
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=400,
                temperature=0,
                system=system,
                messages=state.history,
                tools=[_TOOL],
                tool_choice={"type": "tool", "name": "record_turn"},
            )
        except anthropic.APIError as exc:
            raise VoiceAgentError("Claude request failed") from exc
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == "record_turn":
                data = block.input or {}
                reply = str(data.get("reply") or "").strip()
                if not reply:
                    break
                return AgentDecision(
                    reply=reply,
                    name=_clean(data.get("name")),
                    need=_clean(data.get("need")),
                    requested_start=_clean(data.get("requested_start")),
                    ready_to_book=data.get("ready_to_book") is True,
                )
        raise VoiceAgentError("Claude returned no usable turn")


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:500] or None


# --------------------------------------------------------------------------- #
# Deterministic agent                                                          #
# --------------------------------------------------------------------------- #


def event_id_for_call(call_sid: str) -> str:
    """Google event ids allow [a-v0-9]{5,1024}; a hex digest is valid and stable per call."""
    return hashlib.sha1(call_sid.encode(), usedforsecurity=False).hexdigest()


def parse_start(value: str, tz: ZoneInfo) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed


def spoken_time(start: datetime, tz: ZoneInfo) -> str:
    local = start.astimezone(tz)
    return f"{local:%A, %B} {local.day} at {local.hour % 12 or 12}:{local:%M %p}"


class VoiceAgent:
    def __init__(
        self,
        llm: VoiceLLM,
        calendar: CalendarPort,
        *,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        settings = get_settings()
        self._llm = llm
        self._calendar = calendar
        self._clock = clock
        self._business = settings.voice_business_name
        self._tz_name = settings.voice_timezone
        try:
            self._tz = ZoneInfo(self._tz_name)
        except ZoneInfoNotFoundError:
            logger.warning("voice.bad_timezone", timezone=self._tz_name)
            self._tz_name, self._tz = "UTC", ZoneInfo("UTC")
        self._duration = timedelta(minutes=settings.voice_appointment_minutes)
        self._max_turns = settings.voice_max_turns

    def greeting(self) -> str:
        return GREETING.format(business=self._business)

    async def handle_utterance(self, state: CallState, text: str) -> AgentOutcome:
        if state.status != "collecting":
            return AgentOutcome(reply="", done=True, booked=state.status == "booked")
        state.turns += 1
        if state.history and state.history[-1]["role"] == "user":
            state.history[-1]["content"] += " " + text
        else:
            state.history.append({"role": "user", "content": text})

        now = self._clock()
        try:
            decision = await self._llm.decide(
                state, now=now.astimezone(self._tz), timezone=self._tz_name, business=self._business
            )
        except VoiceAgentError:
            return self._handoff(state, "Sorry, I'm having trouble. Someone will call you back.")

        state.name = decision.name or state.name
        state.need = decision.need or state.need
        if decision.requested_start:
            parsed = parse_start(decision.requested_start, self._tz)
            if parsed is not None:
                state.start = parsed

        reply = decision.reply
        if state.start is not None and state.start <= now:
            state.start = None
            reply = "That time has already passed. What day and time works for you?"
        elif decision.ready_to_book and state.name and state.need and state.start:
            return await self._book(state)
        elif state.turns >= self._max_turns:
            return self._handoff(
                state, "I'm sorry, I couldn't complete that. Someone will call you back."
            )

        state.history.append({"role": "assistant", "content": reply})
        return AgentOutcome(reply=reply, done=False)

    async def _book(self, state: CallState) -> AgentOutcome:
        assert state.start is not None
        end = state.start + self._duration
        try:
            conflicts = await self._calendar.list_events(state.start, end)
            if any(not event.all_day for event in conflicts["items"]):
                state.start = None
                reply = "That time is already taken. What other day or time works for you?"
                state.history.append({"role": "assistant", "content": reply})
                return AgentOutcome(reply=reply, done=False)
            event = await self._calendar.create_event(
                event_id=event_id_for_call(state.call_sid),
                title=f"{state.need} - {state.name}",
                start=state.start,
                end=end,
                description=(
                    f"Booked by the phone assistant.\nCaller: {state.name}\n"
                    f"Phone: {state.caller or 'unknown'}\nNeed: {state.need}\n"
                    f"Call: {state.call_sid}"
                ),
                timezone=self._tz_name,
            )
        except HTTPException as exc:
            logger.warning("voice.booking_failed", status=exc.status_code, call=state.call_sid)
            return self._handoff(
                state, "Sorry, I couldn't book that. Someone will call you back to confirm."
            )
        state.event_id = event.id
        state.status = "booked"
        logger.info("voice.booked", call=state.call_sid, event_id=event.id)
        return AgentOutcome(
            reply=(
                f"You're booked for {spoken_time(state.start, self._tz)}, {state.name}. "
                "Thanks for calling. Goodbye."
            ),
            done=True,
            booked=True,
        )

    @staticmethod
    def _handoff(state: CallState, reply: str) -> AgentOutcome:
        state.status = "handoff"
        return AgentOutcome(reply=reply, done=True)
