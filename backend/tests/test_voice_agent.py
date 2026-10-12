"""Voice agent + Twilio helpers + calendar write. No network: every provider is faked."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.integrations import calendar as calendar_module
from app.integrations.calendar import CalendarEvent, GoogleCalendar
from app.integrations.twilio_voice import (
    TwilioCallResponder,
    compute_signature,
    make_stream_token,
    say_and_hangup_twiml,
    say_and_stream_twiml,
    stream_url,
    verify_signature,
    verify_stream_token,
)
from app.services.voice_agent import (
    AgentDecision,
    CallState,
    ClaudeVoiceLLM,
    VoiceAgent,
    VoiceAgentError,
    event_id_for_call,
    parse_start,
)

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)  # a Monday, 10:00 in Chicago
TOMORROW_3PM = "2026-10-13T15:00:00-05:00"


@pytest.fixture
def settings(monkeypatch):
    from app import config

    s = config.Settings(
        _env_file=None,
        voice_timezone="America/Chicago",
        voice_business_name="Acme Plumbing",
        voice_max_turns=4,
        google_calendar_write_enabled=True,
        google_calendar_client_id="client",
        google_calendar_client_secret=SecretStr("secret"),
        google_calendar_refresh_token=SecretStr("refresh"),
    )
    for module in ("app.services.voice_agent", "app.integrations.calendar"):
        monkeypatch.setattr(f"{module}.get_settings", lambda s=s: s)
    return s


class ScriptedLLM:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.seen_histories = []

    async def decide(self, state, *, now, timezone, business):
        self.seen_histories.append([dict(m) for m in state.history])
        item = self.decisions.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeCalendar:
    def __init__(self, busy=False, fail=None):
        self.busy = busy
        self.fail = fail
        self.created = []

    async def list_events(self, start, end, page_token=None):
        items = (
            [
                CalendarEvent(
                    id="x",
                    title="busy",
                    starts_at=start.isoformat(),
                    all_day=False,
                    participants=[],
                )
            ]
            if self.busy
            else []
        )
        return {"items": items, "next_page_token": None}

    async def create_event(self, **kwargs):
        if self.fail:
            raise self.fail
        self.created.append(kwargs)
        return CalendarEvent(
            id=kwargs["event_id"],
            title=kwargs["title"],
            starts_at=kwargs["start"].isoformat(),
            all_day=False,
            participants=[],
        )


def make_agent(settings, llm, calendar):
    return VoiceAgent(llm, calendar, clock=lambda: NOW)


async def test_collects_slots_over_turns_then_books(settings):
    llm = ScriptedLLM(
        AgentDecision(reply="What do you need?", name="Dana Lee"),
        AgentDecision(reply="When works?", need="water heater leak"),
        AgentDecision(
            reply="Booking tomorrow 3pm for Dana, water heater leak. Correct?",
            requested_start=TOMORROW_3PM,
        ),
        AgentDecision(reply="ignored", ready_to_book=True),
    )
    calendar = FakeCalendar()
    agent = make_agent(settings, llm, calendar)
    state = CallState(call_sid="CA123", caller="+15125550100")

    outcomes = [
        await agent.handle_utterance(state, t)
        for t in ("Hi I'm Dana Lee", "a leak", "tomorrow at 3", "yes")
    ]

    assert [o.done for o in outcomes] == [False, False, False, True]
    assert outcomes[-1].booked and "Tuesday, October 13 at 3:00 PM" in outcomes[-1].reply
    assert state.status == "booked" and len(calendar.created) == 1
    created = calendar.created[0]
    assert created["event_id"] == event_id_for_call("CA123")
    assert created["title"] == "water heater leak - Dana Lee"
    assert created["start"] == datetime(2026, 10, 13, 15, 0, tzinfo=UTC) + timedelta(hours=5)
    assert created["end"] - created["start"] == timedelta(minutes=60)
    assert "+15125550100" in created["description"] and created["timezone"] == "America/Chicago"
    # Roles alternate, starting with the caller, so Claude's messages API accepts the history.
    assert [m["role"] for m in llm.seen_histories[-1]] == ["user", "assistant"] * 3 + ["user"]


async def test_slots_persist_when_later_turns_omit_them(settings):
    llm = ScriptedLLM(
        AgentDecision(reply="a", name="Dana", need="leak", requested_start=TOMORROW_3PM),
        AgentDecision(reply="confirm?", ready_to_book=True),  # no slots repeated
    )
    agent = make_agent(settings, llm, FakeCalendar())
    state = CallState(call_sid="CA1")
    await agent.handle_utterance(state, "x")
    assert (await agent.handle_utterance(state, "yes")).booked


async def test_does_not_book_without_all_three_slots(settings):
    llm = ScriptedLLM(
        AgentDecision(reply="Which day?", name="Dana", need="leak", ready_to_book=True)
    )
    calendar = FakeCalendar()
    outcome = await make_agent(settings, llm, calendar).handle_utterance(
        CallState(call_sid="CA1"), "hello"
    )
    assert not outcome.done and outcome.reply == "Which day?" and not calendar.created


async def test_past_time_is_rejected_and_reasked(settings):
    llm = ScriptedLLM(
        AgentDecision(
            reply="ok",
            name="D",
            need="n",
            ready_to_book=True,
            requested_start="2026-10-12T08:00:00-05:00",
        )
    )
    state = CallState(call_sid="CA1")
    outcome = await make_agent(settings, llm, FakeCalendar()).handle_utterance(state, "now")
    assert "already passed" in outcome.reply and state.start is None and not outcome.done


async def test_naive_timestamps_use_business_timezone(settings):
    parsed = parse_start(
        "2026-10-13T15:00:00", make_agent(settings, ScriptedLLM(), FakeCalendar())._tz
    )
    assert parsed.utcoffset() == timedelta(hours=-5)
    assert (
        parse_start("not a date", make_agent(settings, ScriptedLLM(), FakeCalendar())._tz) is None
    )


async def test_calendar_conflict_asks_for_another_time(settings):
    llm = ScriptedLLM(
        AgentDecision(
            reply="ok", name="D", need="n", ready_to_book=True, requested_start=TOMORROW_3PM
        )
    )
    calendar = FakeCalendar(busy=True)
    state = CallState(call_sid="CA1")
    outcome = await make_agent(settings, llm, calendar).handle_utterance(state, "yes")
    assert "already taken" in outcome.reply and not outcome.done
    assert state.start is None and not calendar.created


async def test_calendar_failure_hands_off_without_claiming_a_booking(settings):
    llm = ScriptedLLM(
        AgentDecision(
            reply="ok", name="D", need="n", ready_to_book=True, requested_start=TOMORROW_3PM
        )
    )
    calendar = FakeCalendar(fail=HTTPException(502, "boom"))
    state = CallState(call_sid="CA1")
    outcome = await make_agent(settings, llm, calendar).handle_utterance(state, "yes")
    assert outcome.done and not outcome.booked and "call you back" in outcome.reply
    assert state.status == "handoff"


async def test_llm_failure_hands_off(settings):
    llm = ScriptedLLM(VoiceAgentError("down"))
    outcome = await make_agent(settings, llm, FakeCalendar()).handle_utterance(
        CallState(call_sid="CA1"), "hi"
    )
    assert outcome.done and not outcome.booked


async def test_turn_limit_hands_off(settings):
    llm = ScriptedLLM(*[AgentDecision(reply="more?") for _ in range(4)])
    agent = make_agent(settings, llm, FakeCalendar())
    state = CallState(call_sid="CA1")
    outcomes = [await agent.handle_utterance(state, "um") for _ in range(4)]
    assert [o.done for o in outcomes] == [False, False, False, True]
    assert state.status == "handoff"


async def test_finished_call_ignores_more_speech(settings):
    state = CallState(call_sid="CA1", status="booked")
    outcome = await make_agent(settings, ScriptedLLM(), FakeCalendar()).handle_utterance(
        state, "hi"
    )
    assert outcome.done and outcome.reply == "" and outcome.booked


# ----------------------------- Claude adapter ------------------------------ #


class FakeAnthropic:
    def __init__(self, content):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self._content = content

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self._content)


def tool_block(**data):
    return SimpleNamespace(type="tool_use", name="record_turn", input=data)


async def test_claude_llm_forces_tool_and_parses_result(settings):
    client = FakeAnthropic(
        [tool_block(reply=" Hi! ", name="Dana", ready_to_book=True, requested_start=TOMORROW_3PM)]
    )
    llm = ClaudeVoiceLLM(client=client, model="claude-test")
    state = CallState(call_sid="CA1", history=[{"role": "user", "content": "hi"}])
    decision = await llm.decide(state, now=NOW, timezone="America/Chicago", business="Acme")
    assert decision == AgentDecision(
        reply="Hi!", name="Dana", need=None, requested_start=TOMORROW_3PM, ready_to_book=True
    )
    call = client.calls[0]
    assert call["model"] == "claude-test"
    assert call["tool_choice"] == {"type": "tool", "name": "record_turn"}
    assert "2026-10-12T15:00" in call["system"] and "Acme" in call["system"]


async def test_claude_llm_rejects_missing_tool_and_non_boolean_ready(settings):
    llm = ClaudeVoiceLLM(client=FakeAnthropic([SimpleNamespace(type="text", text="hi")]))
    with pytest.raises(VoiceAgentError):
        await llm.decide(CallState(call_sid="x"), now=NOW, timezone="UTC", business="b")
    llm = ClaudeVoiceLLM(client=FakeAnthropic([tool_block(reply="ok", ready_to_book="true")]))
    decision = await llm.decide(CallState(call_sid="x"), now=NOW, timezone="UTC", business="b")
    assert decision.ready_to_book is False  # only a real boolean true may trigger a booking


def test_claude_llm_requires_api_key(settings):
    with pytest.raises(VoiceAgentError):
        ClaudeVoiceLLM()


# ------------------------------- Twilio helpers ----------------------------- #

TWILIO_PARAMS = [
    ("CallSid", "CA1234567890ABCDE"),
    ("Caller", "+14158675310"),
    ("Digits", "1234"),
    ("From", "+14158675310"),
    ("To", "+18005551212"),
]
TWILIO_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"


def test_signature_known_answer_and_order_independence():
    signature = compute_signature("12345", TWILIO_URL, TWILIO_PARAMS)
    assert signature == "GvWf1cFY/Q7PnoempGyD5oXAezc="
    assert verify_signature("12345", TWILIO_URL, list(reversed(TWILIO_PARAMS)), signature)


@pytest.mark.parametrize(
    ("token", "url", "signature"),
    [
        ("wrong", TWILIO_URL, "GvWf1cFY/Q7PnoempGyD5oXAezc="),
        ("12345", TWILIO_URL + "x", "GvWf1cFY/Q7PnoempGyD5oXAezc="),
        ("12345", TWILIO_URL, "tampered"),
        ("12345", TWILIO_URL, None),
        ("", TWILIO_URL, "GvWf1cFY/Q7PnoempGyD5oXAezc="),
    ],
)
def test_signature_rejections(token, url, signature):
    assert not verify_signature(token, url, TWILIO_PARAMS, signature)


def test_signature_rejects_changed_params():
    sig = compute_signature("12345", TWILIO_URL, TWILIO_PARAMS)
    assert not verify_signature("12345", TWILIO_URL, [*TWILIO_PARAMS, ("Extra", "1")], sig)


def test_stream_token_binding_and_expiry():
    token = make_stream_token("secret", "CA42", now=1000)
    assert verify_stream_token("secret", token, now=1001) == "CA42"
    assert verify_stream_token("secret", token, now=1000 + 3601) is None
    assert verify_stream_token("other", token, now=1001) is None
    sid, exp, mac = token.rsplit(".", 2)
    assert verify_stream_token("secret", f"CA43.{exp}.{mac}", now=1001) is None
    assert verify_stream_token("secret", "garbage", now=1001) is None
    assert verify_stream_token("", token, now=1001) is None


def test_twiml_escapes_and_stream_url_is_wss():
    url = stream_url("https://abc.example.com/", "secret", "CA1")
    assert url.startswith("wss://abc.example.com/voice/twilio/stream?token=CA1.")
    xml = say_and_stream_twiml("Tom & Jerry <3", url, "CA1")
    assert "Tom &amp; Jerry &lt;3" in xml and '<Parameter name="callSid" value="CA1"/>' in xml
    assert say_and_hangup_twiml("bye") == "<Response><Say>bye</Say><Hangup/></Response>"


async def test_responder_posts_twiml_to_call_with_basic_auth():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    responder = TwilioCallResponder(
        account_sid="AC1",
        auth_token="tok",
        public_base_url="https://x.example.com",
        transport=httpx.MockTransport(handler),
    )
    await responder.reply("CA9", "Hello", reconnect=True)
    await responder.reply("CA9", "Bye", reconnect=False)
    first, second = seen
    assert first.url.path == "/2010-04-01/Accounts/AC1/Calls/CA9.json"
    assert first.headers["authorization"].startswith("Basic ")
    assert "Stream" in first.content.decode() and "Hangup" not in first.content.decode()
    assert "Hangup" in second.content.decode()


async def test_responder_error_does_not_leak_body():
    responder = TwilioCallResponder(
        account_sid="AC1",
        auth_token="tok",
        public_base_url="https://x",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret-body AC1")),
    )
    with pytest.raises(RuntimeError) as err:
        await responder.reply("CA9", "Hello", reconnect=False)
    assert "secret-body" not in str(err.value)


# ------------------------------- Calendar write ----------------------------- #


def calendar_transport(insert_status=200, record=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at"})
        if record is not None:
            record.append(request)
        if request.method == "POST":
            if insert_status != 200:
                return httpx.Response(insert_status, json={})
            return httpx.Response(200, json={**json.loads(request.content), "status": "confirmed"})
        return httpx.Response(
            200,
            json={
                "id": "evt1",
                "summary": "existing",
                "start": {"dateTime": "2026-10-13T15:00:00-05:00"},
            },
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_httpx(monkeypatch):
    real = httpx.AsyncClient

    def install(transport):
        monkeypatch.setattr(
            calendar_module.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw)
        )

    return install


async def test_create_event_posts_idempotent_id_and_times(settings, patched_httpx):
    sent = []
    patched_httpx(calendar_transport(record=sent))
    start = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    event = await GoogleCalendar().create_event(
        event_id="abc12",
        title="Leak - Dana",
        start=start,
        end=start + timedelta(hours=1),
        timezone="America/Chicago",
    )
    (request,) = sent
    body = json.loads(request.content)
    assert request.method == "POST" and request.url.params["sendUpdates"] == "none"
    assert body["id"] == "abc12" and body["start"]["timeZone"] == "America/Chicago"
    assert request.headers["authorization"] == "Bearer at"
    assert event.title == "Leak - Dana"


async def test_create_event_duplicate_returns_existing(settings, patched_httpx):
    patched_httpx(calendar_transport(insert_status=409))
    start = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    event = await GoogleCalendar().create_event(
        event_id="abc12", title="t", start=start, end=start + timedelta(hours=1)
    )
    assert event.id == "evt1"  # retried call sees the earlier booking instead of double-booking


async def test_create_event_requires_opt_in_and_aware_times(settings):
    start = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        await GoogleCalendar().create_event(
            event_id="abc12", title="t", start=start.replace(tzinfo=None), end=start
        )
    settings.google_calendar_write_enabled = False
    with pytest.raises(HTTPException) as err:
        await GoogleCalendar().create_event(event_id="abc12", title="t", start=start, end=start)
    assert err.value.status_code == 503


async def test_create_event_upstream_failure_is_sanitised(settings, patched_httpx):
    patched_httpx(calendar_transport(insert_status=500))
    start = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    with pytest.raises(HTTPException) as err:
        await GoogleCalendar().create_event(event_id="abc12", title="t", start=start, end=start)
    assert err.value.status_code == 502
