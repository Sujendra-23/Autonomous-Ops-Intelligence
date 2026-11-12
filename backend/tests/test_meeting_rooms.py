"""Scheduling edge cases, workspace-bound call credentials and safe invitation retries."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from app.api import meetings
from app.config import Settings
from app.integrations import calendar
from app.models.meeting import MeetingRoom
from app.schemas.meeting import ScheduleMeeting
from app.services import calls
from app.tenancy import principal_context, workspace_context


def schedule(**changes):
    return ScheduleMeeting(
        request_id=uuid.uuid4(),
        title="Planning",
        meeting_date="2099-06-01",
        start_time="09:30",
        time_zone="America/Los_Angeles",
        **changes,
    )


def room(**changes):
    return MeetingRoom(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        title="Planning",
        kind="meeting",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        ends_at=datetime.now(UTC) + timedelta(days=1, hours=1),
        time_zone="UTC",
        description="",
        attendees=[],
        reminder_minutes=10,
        invitation_status="not_requested",
        **changes,
    )


def test_timezone_and_email_validation():
    payload = schedule(attendees=[" ALEX@example.com ", "alex@example.com"])
    assert payload.attendees == ["alex@example.com"]
    assert payload.start().hour == 9 and payload.start().utcoffset() == timedelta(hours=-7)
    assert (payload.end().astimezone(UTC) - payload.start().astimezone(UTC)).total_seconds() == 1800
    for changes in (
        {"time_zone": "Invalid/Zone"},
        {"attendees": ["not-an-email"]},
        {"duration_minutes": 0},
        {"duration_minutes": 2000},
    ):
        base = payload.model_dump()
        base.update(changes)
        with pytest.raises(ValidationError):
            ScheduleMeeting.model_validate(base)


def test_dst_gaps_and_ambiguous_times_are_rejected():
    for month, occurrence in [(3, 2), (11, 1)]:
        first = date(2099, month, 1)
        sunday = first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (occurrence - 1))
        values = schedule().model_dump()
        values.update(meeting_date=sunday, start_time="02:30" if month == 3 else "01:30")
        with pytest.raises(ValidationError, match="daylight saving"):
            ScheduleMeeting.model_validate(values)


async def test_tokens_are_short_lived_room_scoped_and_huddles_exclude_camera(monkeypatch):
    config = Settings(
        _env_file=None,
        livekit_url="ws://localhost:7880",
        livekit_api_key="fixture-key",
        livekit_api_secret=SecretStr("x" * 40),
    )
    monkeypatch.setattr(calls, "get_settings", lambda: config)
    rpc = AsyncMock(return_value={})
    monkeypatch.setattr(calls, "rpc", rpc)
    meeting = room()
    meeting.kind = "huddle"
    token = await calls.join(meeting, "Alice")
    decoded = jwt.decode(token["token"], "x" * 40, algorithms=["HS256"])
    assert decoded["video"]["room"] == meeting.media_room
    assert decoded["video"]["roomJoin"]
    assert "camera" not in decoded["video"]["canPublishSources"]
    assert "screen_share" in decoded["video"]["canPublishSources"]
    assert not decoded["video"].get("roomAdmin")
    assert decoded["exp"] - decoded["nbf"] <= 125
    assert "secret" not in token and token["server_url"] == config.livekit_url
    meeting.kind = "meeting"
    decoded = jwt.decode(
        (await calls.join(meeting, "Alice"))["token"], "x" * 40, algorithms=["HS256"]
    )
    assert "camera" in decoded["video"]["canPublishSources"]


async def test_missing_call_configuration_is_actionable(monkeypatch):
    monkeypatch.setattr(calls, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(HTTPException) as exc:
        calls.signed_token({"roomJoin": True})
    assert exc.value.status_code == 503


async def test_room_lookup_always_includes_current_workspace():
    workspace = uuid.uuid4()
    marker = workspace_context.set(workspace)
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    try:
        with pytest.raises(HTTPException) as exc:
            await meetings.find(db, uuid.uuid4())
        assert exc.value.status_code == 404
        sql = db.scalar.call_args.args[0].compile()
        assert workspace in sql.params.values()
    finally:
        workspace_context.reset(marker)


async def test_existing_attempt_cannot_be_changed_or_create_another_room():
    existing = room()
    db = MagicMock(scalar=AsyncMock(return_value=existing), commit=AsyncMock())
    result, fresh = await meetings.save(
        db, existing.request_id, title=existing.title, kind=existing.kind
    )
    assert result is existing and not fresh
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    with pytest.raises(HTTPException) as exc:
        await meetings.save(db, existing.request_id, title="Different", kind=existing.kind)
    assert exc.value.status_code == 409


async def test_calendar_idempotency_uses_workspace_and_does_not_reinvite(monkeypatch):
    config = Settings(
        _env_file=None,
        google_calendar_client_id="client",
        google_calendar_client_secret=SecretStr("fixture-secret"),
        google_calendar_refresh_token=SecretStr("fixture-refresh"),
    )
    monkeypatch.setattr(calendar, "get_settings", lambda: config)
    original = httpx.AsyncClient
    events = {}
    writes = []

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "fixture-access"})
        assert request.headers["authorization"] == "Bearer fixture-access"
        if request.method == "POST":
            payload = json.loads(request.content)
            assert request.url.params["sendUpdates"] == "all"
            assert "conferenceData" not in payload
            assert payload["location"] == "https://aoi.example/meetings/rooms/fixture"
            if payload["id"] in events:
                return httpx.Response(409, json={})
            writes.append(payload)
            events[payload["id"]] = {
                **payload,
                "htmlLink": "https://calendar.google.com/calendar/event?eid=fixture",
            }
            return httpx.Response(200, json=events[payload["id"]])
        return httpx.Response(200, json=events[request.url.path.split("/")[-1]])

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler))
    )
    client = calendar.GoogleCalendar()
    payload = schedule(attendees=["alex@example.test"])
    url = "https://aoi.example/meetings/rooms/fixture"
    first = await client.schedule(payload, aoi_url=url)
    second = await client.schedule(payload, aoi_url=url)
    assert first.id == second.id and len(writes) == 1
    marker = principal_context.set(SimpleNamespace(workspace_id=uuid.uuid4()))
    try:
        third = await client.schedule(payload, aoi_url=url)
    finally:
        principal_context.reset(marker)
    assert third.id != first.id and len(writes) == 2


async def test_optional_calendar_failure_keeps_aoi_meeting(monkeypatch):
    meeting = room()
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(
        meetings, "get_settings", lambda: SimpleNamespace(app_public_url="https://aoi.example")
    )
    monkeypatch.setattr(
        calendar.GoogleCalendar,
        "schedule",
        AsyncMock(side_effect=HTTPException(502, "Synthetic outage")),
    )
    await meetings.invite(db, meeting, schedule())
    assert meeting.invitation_status == "failed"
    assert meeting.id
    db.commit.assert_awaited_once()


async def test_presence_failure_stays_unknown_not_empty(monkeypatch):
    monkeypatch.setattr(calls, "enabled", lambda: True)
    monkeypatch.setattr(calls, "rpc", AsyncMock(side_effect=HTTPException(503, "offline")))
    assert await calls.presence([room()]) is None


@pytest.mark.parametrize("field", ["num_participants", "numParticipants"])
async def test_presence_reads_provider_counts(monkeypatch, field):
    meeting = room()
    monkeypatch.setattr(calls, "enabled", lambda: True)
    monkeypatch.setattr(
        calls, "rpc", AsyncMock(return_value={"rooms": [{"name": meeting.media_room, field: 2}]})
    )
    assert (await calls.presence([meeting]))[meeting.media_room] == 2


async def test_viewers_can_join_but_cannot_create_or_send_invitations(monkeypatch):
    from cryptography.fernet import Fernet

    from app import security

    config = Settings(_env_file=None)
    config.auth_mode = "oidc"
    config.connector_encryption_key = SecretStr(Fernet.generate_key().decode())
    monkeypatch.setattr(security, "base_settings", lambda: config)
    monkeypatch.setattr(security, "workspace_settings", lambda _: config)
    principal = SimpleNamespace(
        workspace_id=uuid.uuid4(), account_id=uuid.uuid4(), role="viewer", connector_ciphertext=None
    )
    monkeypatch.setattr(security, "authenticate", AsyncMock(return_value=principal))

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = security.SecurityMiddleware(app)
    middleware.redis.eval = AsyncMock(return_value=1)
    identifier = str(uuid.uuid4())
    for path, expected in [
        (f"/api/meetings/{identifier}/join", 200),
        ("/api/meetings", 403),
        (f"/api/meetings/{identifier}/invitations", 403),
        ("/api/tasks", 403),
    ]:
        messages = []

        async def send(message, target=messages):
            target.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await middleware(
            {
                "type": "http",
                "path": path,
                "method": "POST",
                "headers": [(b"authorization", b"Bearer fixture-credential")],
            },
            receive,
            send,
        )
        assert (
            next(message for message in messages if message["type"] == "http.response.start")[
                "status"
            ]
            == expected
        )


async def test_voice_invite_aliases_preserve_intent_without_provider_pii(monkeypatch):
    from app.api import assistant

    plan = AsyncMock(
        return_value=json.dumps(
            {
                "message": "Fill the invitation",
                "actions": [{"kind": "fill", "target": "c0", "value": "AOI_INVITEE_0"}],
                "done": False,
            }
        )
    )
    monkeypatch.setattr(assistant, "get_llm_client", lambda: SimpleNamespace(plan=plan))
    payload = assistant.Command(
        utterance="Schedule planning and invite alex@example.test",
        view="meetings",
        page_text="Schedule a meeting",
        controls=[assistant.Control(id="c0", role="textarea", label="Teammate emails")],
    )
    result = await assistant.command(payload)
    assert "alex@example.test" not in plan.call_args.args[1]
    assert "AOI_INVITEE_0" in plan.call_args.args[1]
    assert result.actions[0].value == "alex@example.test"
    plan.return_value = json.dumps(
        {
            "message": "Fill",
            "actions": [{"kind": "fill", "target": "c0", "value": "invented@example.test"}],
            "done": False,
        }
    )
    with pytest.raises(HTTPException) as exc:
        await assistant.command(payload)
    assert exc.value.status_code == 422
