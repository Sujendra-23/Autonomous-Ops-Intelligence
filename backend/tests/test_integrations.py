"""Integration contracts tested without contacting external services."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.integrations import calendar, task_sync, webhooks
from app.models.integration import WebhookDelivery
from app.models.task import Task


@pytest.fixture
def settings(monkeypatch):
    config = Settings(_env_file=None)
    for module in (calendar, task_sync, webhooks):
        monkeypatch.setattr(module, "get_settings", lambda: config)
    return config


def task(**kwargs):
    return Task(
        id=uuid.uuid4(),
        title="Ship change",
        status="open",
        priority="medium",
        sync_pending=False,
        **kwargs,
    )


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )


def test_webhook_config_rejects_unsafe_or_unsigned_destination():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, webhook_url="http://example.com", webhook_secret="x" * 32)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, webhook_url="https://example.com", webhook_secret="short")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, linear_status_map={"done": "x", "open": "x"})


def test_outbox_disabled_and_subscriptions(settings):
    session = MagicMock()
    webhooks.enqueue_event(session, "task.created", {"id": "task"})
    session.add.assert_not_called()
    from pydantic import SecretStr

    settings.webhook_url = SecretStr("https://example.com")
    webhooks.enqueue_event(session, "ignored.event", {})
    session.add.assert_not_called()
    webhooks.enqueue_event(session, "task.created", {"id": "task"})
    event = session.add.call_args.args[0]
    assert event.payload["id"] == str(event.id)
    assert event.payload["data"] == {"id": "task"}


@pytest.mark.parametrize(
    "status,expected",
    [(200, "delivered"), (503, "pending"), (429, "pending"), (400, "failed"), (302, "pending")],
)
async def test_webhook_delivery_signs_exact_body_and_retries(
    settings, monkeypatch, status, expected
):
    from pydantic import SecretStr

    settings.webhook_url = SecretStr("https://example.com/hook")
    settings.webhook_secret = SecretStr("secret" * 8)
    row = WebhookDelivery(
        id=uuid.uuid4(),
        destination="https://example.com/hook",
        event_type="task.created",
        payload={"title": "Café"},
        status="pending",
        attempts=0,
        next_attempt_at=datetime.now(UTC),
    )

    def handler(request):
        signature = hmac.new(
            b"secret" * 8,
            request.headers["X-AOI-Timestamp"].encode() + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-AOI-Signature"] == "sha256=" + signature
        assert request.headers["X-AOI-Event-ID"] == str(row.id)
        assert json.loads(request.content) == row.payload
        return httpx.Response(status, headers={"Location": "https://other.example"})

    mock_http(monkeypatch, handler)
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[row, None])
    session.commit = AsyncMock()
    await webhooks.deliver_webhooks(session)
    assert row.status == expected
    assert row.attempts == 1
    if expected == "pending":
        assert row.next_attempt_at > datetime.now(UTC)


async def test_webhook_max_attempts_and_changed_destination(settings, monkeypatch):
    from pydantic import SecretStr

    settings.webhook_url = SecretStr("https://example.com/hook")
    settings.webhook_secret = SecretStr("x" * 32)
    mock_http(monkeypatch, lambda request: httpx.Response(503))
    row = WebhookDelivery(
        id=uuid.uuid4(),
        destination="https://example.com/hook",
        payload={},
        status="pending",
        attempts=7,
    )
    session = MagicMock(scalar=AsyncMock(side_effect=[row, None]), commit=AsyncMock())
    await webhooks.deliver_webhooks(session)
    assert row.status == "failed"
    row.destination = "https://old.example/hook"
    row.status = "pending"
    session.scalar = AsyncMock(side_effect=[row, None])
    await webhooks.deliver_webhooks(session)
    assert row.status == "failed"
    assert "Destination changed" in row.last_error
    assert row.attempts == 8


@pytest.mark.parametrize(
    "category,expected",
    [
        ("completed", "done"),
        ("canceled", "cancelled"),
        ("started", "in_progress"),
        ("backlog", "open"),
    ],
)
def test_linear_status_mapping(category, expected):
    assert task_sync.mapped_status({"id": "s", "type": category}, {}, linear=True) == expected
    assert (
        task_sync.mapped_status({"id": "s", "type": category}, {"blocked": "s"}, linear=True)
        == "blocked"
    )


async def test_linear_push_uses_team_state_and_never_guesses_blocked(settings):
    from pydantic import SecretStr

    settings.linear_api_key = SecretStr("test-key")
    settings.linear_team_id = "team"
    row = task(linear_issue_id="issue")
    row.status, row.sync_pending = "done", True
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "mutation" in body["query"]:
            return httpx.Response(200, json={"data": {"issueUpdate": {"success": True}}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "issue": {
                        "state": {"id": "open", "type": "unstarted"},
                        "team": {"states": {"nodes": [{"id": "done-state", "type": "completed"}]}},
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = task_sync.StatusClient(client)
        assert await adapter.linear(row) == "done"
        assert calls[-1]["variables"]["state"] == "done-state"
        row.status = "blocked"
        with pytest.raises(task_sync.SyncError, match="LINEAR_STATUS_MAP"):
            await adapter.linear(row)


async def test_jira_push_uses_transition_id_not_status_id(settings):
    from pydantic import SecretStr

    settings.jira_base_url = "https://jira.example"
    settings.jira_email = "test@example.com"
    settings.jira_api_token = SecretStr("test-key")
    settings.jira_status_map = {"done": "status-done"}
    row = task(jira_issue_key="AOI-1")
    row.status, row.sync_pending = "done", True

    def handler(request):
        if request.method == "POST":
            assert json.loads(request.content) == {"transition": {"id": "transition-42"}}
            return httpx.Response(204)
        if request.url.path.endswith("transitions"):
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {
                            "id": "transition-42",
                            "to": {"id": "status-done", "statusCategory": {"key": "done"}},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"fields": {"status": {"id": "status-open", "statusCategory": {"key": "new"}}}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await task_sync.StatusClient(client).jira(row) == "done"


async def test_failed_push_preserves_local_status_and_pending_flag(settings, monkeypatch):
    settings.task_sync_enabled = True
    row = task(linear_issue_id="issue")
    row.status, row.sync_pending = "done", True
    monkeypatch.setattr(
        task_sync.StatusClient, "linear", AsyncMock(side_effect=task_sync.SyncError("Try later"))
    )
    session = MagicMock(scalar=AsyncMock(side_effect=[row, None]), commit=AsyncMock())
    await task_sync.sync_task_statuses(session)
    assert row.status == "done" and row.sync_pending
    assert row.sync_error == "Try later"
    assert row.sync_checked_at is not None


async def test_pull_updates_local_state_and_audits_once(settings, monkeypatch):
    settings.task_sync_enabled = True
    row = task(linear_issue_id="issue")
    monkeypatch.setattr(task_sync.StatusClient, "linear", AsyncMock(return_value="done"))
    session = MagicMock(scalar=AsyncMock(side_effect=[row, None]), commit=AsyncMock())
    assert await task_sync.sync_task_statuses(session) == 1
    assert row.status == "done" and not row.sync_pending
    assert session.add.call_args.args[0].kind == "external_status_change"
    assert row.last_status_change_at is not None


async def test_calendar_oauth_normalization_and_pagination(settings, monkeypatch):
    from pydantic import SecretStr

    settings.google_calendar_client_id = "client"
    settings.google_calendar_client_secret = SecretStr("secret")
    settings.google_calendar_refresh_token = SecretStr("refresh")
    event = {
        "id": "event",
        "summary": "Weekly review",
        "start": {"dateTime": "2026-10-04T09:00:00-07:00"},
        "recurringEventId": "series",
        "attendees": [
            {"email": "a@example.com", "displayName": "Alex"},
            {"email": "b@example.com", "responseStatus": "declined"},
        ],
    }

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            assert b"grant_type=refresh_token" in request.content
            return httpx.Response(200, json={"access_token": "access"})
        assert request.headers["Authorization"] == "Bearer access"
        if request.url.path.endswith("/events/event"):
            return httpx.Response(200, json=event)
        assert request.url.params["pageToken"] == "next"
        return httpx.Response(
            200, json={"items": [event, {"status": "cancelled"}], "nextPageToken": "more"}
        )

    mock_http(monkeypatch, handler)
    response = await calendar.GoogleCalendar().list_events(
        datetime.now(UTC), datetime.now(UTC), "next"
    )
    assert response["next_page_token"] == "more"
    assert response["items"][0].participants == ["Alex"]
    context = await calendar.meeting_context("event")
    assert context["meeting_date"].hour == 16
    assert context["calendar_context"]["recurring_event_id"] == "series"


async def test_calendar_disabled_and_upstream_error_are_actionable(settings, monkeypatch):
    with pytest.raises(HTTPException) as error:
        await calendar.GoogleCalendar().get_event("event")
    assert error.value.status_code == 503
    from pydantic import SecretStr

    settings.google_calendar_client_id = "client"
    settings.google_calendar_client_secret = SecretStr("secret")
    settings.google_calendar_refresh_token = SecretStr("refresh")
    mock_http(monkeypatch, lambda request: httpx.Response(401, text="private-token"))
    with pytest.raises(HTTPException) as error:
        await calendar.GoogleCalendar().get_event("event")
    assert error.value.status_code == 502
    assert "private-token" not in error.value.detail


async def test_all_day_has_no_invented_time(settings, monkeypatch):
    monkeypatch.setattr(
        calendar.GoogleCalendar,
        "get_event",
        AsyncMock(
            return_value=calendar.normalize_event({"id": "day", "start": {"date": "2026-10-04"}})
        ),
    )
    context = await calendar.meeting_context("day")
    assert context["meeting_date"] is None
    assert context["calendar_context"]["starts_at"] == "2026-10-04"


async def test_calendar_recurring_project_reused_unless_hint_given(settings, monkeypatch):
    from app.models.project import Project

    project = Project(id=uuid.uuid4(), name="API", slug="api")
    session = MagicMock(
        scalar=AsyncMock(return_value=project.id), get=AsyncMock(return_value=project)
    )
    context = {"calendar_context": {"calendar_id": "primary", "recurring_event_id": "series"}}
    assert await calendar.resolve_meeting_project(session, context, None) is project
    resolve = AsyncMock(return_value=project)
    monkeypatch.setattr(calendar, "get_or_create_project", resolve)
    assert await calendar.resolve_meeting_project(session, context, "Override") is project
    resolve.assert_awaited_once_with(session, "Override")


async def test_api_auth_and_calendar_date_validation(settings, monkeypatch):
    from pydantic import SecretStr

    from app.api import deps, integrations
    from app.main import app

    settings.ingest_api_key = SecretStr("local-test-key")
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(integrations, "get_settings", lambda: settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.get("/api/integrations/status")
        assert denied.status_code == 401
        headers = {"X-API-Key": "local-test-key"}
        response = await client.get("/api/integrations/status", headers=headers)
        assert response.status_code == 200
        assert "local-test-key" not in response.text
        assert "webhook_secret" not in response.text
        naive = await client.get(
            "/api/integrations/calendar/events?start=2026-10-04T09:00:00", headers=headers
        )
        assert naive.status_code == 422
        task_denied = await client.patch(f"/api/tasks/{uuid.uuid4()}", json={"status": "done"})
        assert task_denied.status_code == 401


async def test_task_edit_commits_pending_status_with_event(settings, monkeypatch):
    from pydantic import SecretStr

    from app.api.tasks import TaskUpdate, update_task

    settings.task_sync_enabled = True
    settings.webhook_url = SecretStr("https://example.com")
    row = task(linear_issue_id="issue")
    row.created_at = row.last_status_change_at = datetime.now(UTC)
    session = MagicMock(get=AsyncMock(return_value=row), commit=AsyncMock(), refresh=AsyncMock())
    result = await update_task(row.id, TaskUpdate(status="done"), session)
    assert result.status == "done" and result.sync_pending
    session.get.assert_awaited_once_with(Task, row.id, with_for_update=True)
    events = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], WebhookDelivery)
    ]
    assert events[0].payload["data"]["changes"]["status"] == {"from": "open", "to": "done"}
    session.commit.assert_awaited_once()


async def test_outbox_rolls_back_with_source_transaction(db_session, settings):
    from pydantic import SecretStr
    from sqlalchemy import select

    settings.webhook_url = SecretStr("https://example.com")
    row = task()
    db_session.add(row)
    webhooks.enqueue_event(db_session, "task.created", webhooks.task_data(row))
    await db_session.flush()
    await db_session.rollback()
    assert await db_session.get(Task, row.id) is None
    assert await db_session.scalar(select(WebhookDelivery)) is None


async def test_retry_preserves_event_id(settings, monkeypatch):
    from pydantic import SecretStr

    from app.api import integrations

    settings.webhook_url = SecretStr("https://example.com")
    monkeypatch.setattr(integrations, "get_settings", lambda: settings)
    row = WebhookDelivery(
        id=uuid.uuid4(), destination="https://example.com", status="failed", attempts=8
    )
    session = MagicMock(get=AsyncMock(return_value=row), commit=AsyncMock())
    result = await integrations.retry_delivery(row.id, session)
    assert result == {"id": str(row.id), "status": "pending"}
    assert row.attempts == 0
    with pytest.raises(HTTPException) as error:
        await integrations.retry_delivery(row.id, session)
    assert error.value.status_code == 409
