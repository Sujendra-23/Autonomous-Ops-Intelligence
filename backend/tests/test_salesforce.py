"""Salesforce adapter contracts, tested without contacting Salesforce."""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.integrations import salesforce
from app.integrations.base import DispatchResult, TaskMirror
from app.integrations.dispatcher import IntegrationDispatcher
from app.integrations.salesforce import SalesforceAdapter
from app.models.project import Project
from app.models.task import Task

INSTANCE = "https://example.my.salesforce.com"


@pytest.fixture
def settings(monkeypatch):
    config = Settings(
        _env_file=None,
        salesforce_instance_url=INSTANCE,
        salesforce_client_id="consumer-key",
        salesforce_client_secret=SecretStr("consumer-secret"),
        salesforce_refresh_token=SecretStr("refresh-secret"),
    )
    monkeypatch.setattr(salesforce, "get_settings", lambda: config)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("asyncio.sleep", no_sleep)
    return config


def task(**kwargs):
    fields = {"title": "Send the revised quote", "status": "open", "priority": "high"}
    fields.update(kwargs)
    return Task(id=uuid.uuid4(), **fields)


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )


def token_response():
    return httpx.Response(200, json={"access_token": "access-1", "instance_url": INSTANCE})


def created_response(record_id="00T000000000001AAA"):
    return httpx.Response(201, json={"id": record_id, "success": True, "errors": []})


def test_config_requires_all_credentials_and_a_salesforce_https_url():
    assert Settings(_env_file=None).salesforce_enabled is False
    partial = Settings(_env_file=None, salesforce_instance_url=INSTANCE, salesforce_client_id="x")
    assert partial.salesforce_enabled is False
    for bad in (
        "http://example.my.salesforce.com",
        "https://evil.example.com",
        "https://example.my.salesforce.com.evil.com",
        "https://user:pw@example.my.salesforce.com",
        "https://example.my.salesforce.com/path",
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, salesforce_instance_url=bad)


async def test_creates_task_record_with_refresh_token_grant(settings, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/services/oauth2/token":
            return token_response()
        return created_response()

    mock_http(monkeypatch, handler)
    row = task(
        title="x" * 300,
        description="Details",
        owner="Dana",
        source_quote="  We will send it Friday  ",
        due_date=datetime(2026, 11, 6, 17, 0, tzinfo=UTC),
    )
    result = await SalesforceAdapter().create_task(row, Project(name="Acme renewal"))

    token_call, create_call = calls
    assert str(token_call.url) == "https://login.salesforce.com/services/oauth2/token"
    assert parse_qs(token_call.content.decode()) == {
        "grant_type": ["refresh_token"],
        "client_id": ["consumer-key"],
        "client_secret": ["consumer-secret"],
        "refresh_token": ["refresh-secret"],
    }
    assert str(create_call.url) == f"{INSTANCE}/services/data/v60.0/sobjects/Task"
    assert create_call.headers["Authorization"] == "Bearer access-1"
    body = json.loads(create_call.content)
    assert len(body["Subject"]) == 255
    assert (body["Status"], body["Priority"], body["ActivityDate"]) == (
        "Not Started",
        "High",
        "2026-11-06",
    )
    assert "We will send it Friday" in body["Description"]
    assert "Inferred owner: Dana" in body["Description"]
    assert "Project: Acme renewal" in body["Description"]
    assert result.success and result.external_id == "00T000000000001AAA"
    assert row.salesforce_task_id == "00T000000000001AAA"
    assert row.salesforce_task_url == f"{INSTANCE}/lightning/r/Task/00T000000000001AAA/view"


async def test_sandbox_authenticates_at_test_login_host(settings, monkeypatch):
    settings.salesforce_sandbox = True
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return token_response() if request.url.path.endswith("/token") else created_response()

    mock_http(monkeypatch, handler)
    assert (await SalesforceAdapter().create_task(task(), None)).success
    assert hosts[0] == "test.salesforce.com"


async def test_priority_mapping_and_no_due_date(settings, monkeypatch):
    bodies = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return token_response()
        bodies.append(json.loads(request.content))
        return created_response()

    mock_http(monkeypatch, handler)
    for priority in ("urgent", "medium", "low"):
        await SalesforceAdapter().create_task(task(priority=priority), None)
    assert [b["Priority"] for b in bodies] == ["High", "Normal", "Low"]
    assert all("ActivityDate" not in b for b in bodies)


async def test_existing_record_and_disabled_make_no_requests(settings, monkeypatch):
    mock_http(monkeypatch, lambda request: pytest.fail("no HTTP expected"))
    existing = await SalesforceAdapter().create_task(task(salesforce_task_id="00T1"), None)
    assert existing.success and existing.detail == "exists"
    settings.salesforce_refresh_token = SecretStr("")
    disabled = await SalesforceAdapter().create_task(task(), None)
    assert not disabled.success and disabled.detail == "disabled"


async def test_expired_access_token_is_refreshed_once(settings, monkeypatch):
    tokens = iter(["stale", "fresh"])
    seen = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": next(tokens)})
        seen.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer stale":
            return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
        return created_response()

    mock_http(monkeypatch, handler)
    assert (await SalesforceAdapter().create_task(task(), None)).success
    assert seen == ["Bearer stale", "Bearer fresh"]


async def test_second_401_after_refresh_fails_without_looping(settings, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/token"):
            return token_response()
        return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])

    mock_http(monkeypatch, handler)
    result = await SalesforceAdapter().create_task(task(), None)
    assert not result.success and "HTTP 401 INVALID_SESSION_ID" in result.detail
    assert len(calls) == 4  # token, create, token, create


@pytest.mark.parametrize("status", [429, 503])
async def test_transient_failures_retry_then_succeed(settings, monkeypatch, status):
    attempts = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return token_response()
        attempts.append(1)
        return httpx.Response(status) if len(attempts) < 3 else created_response()

    mock_http(monkeypatch, handler)
    assert (await SalesforceAdapter().create_task(task(), None)).success
    assert len(attempts) == 3


async def test_network_errors_stop_after_three_attempts(settings, monkeypatch):
    attempts = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return token_response()
        attempts.append(1)
        raise httpx.ConnectError("boom")

    mock_http(monkeypatch, handler)
    row = task()
    result = await SalesforceAdapter().create_task(row, None)
    assert not result.success and len(attempts) == 3
    assert row.salesforce_task_id is None


async def test_client_errors_are_not_retried_and_leak_nothing(settings, monkeypatch):
    attempts = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return token_response()
        attempts.append(1)
        return httpx.Response(
            400,
            json=[{"errorCode": "REQUIRED_FIELD_MISSING", "message": "secret customer text"}],
        )

    mock_http(monkeypatch, handler)
    result = await SalesforceAdapter().create_task(task(), None)
    assert not result.success and len(attempts) == 1
    assert result.detail == "Salesforce create failed (HTTP 400 REQUIRED_FIELD_MISSING)"
    assert "secret customer text" not in result.detail


async def test_invalid_refresh_token_fails_cleanly_without_secrets(settings, monkeypatch):
    mock_http(
        monkeypatch,
        lambda request: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    result = await SalesforceAdapter().create_task(task(), None)
    assert not result.success
    assert result.detail == "Salesforce authentication failed (HTTP 400 invalid_grant)"
    for secret in ("consumer-secret", "refresh-secret"):
        assert secret not in result.detail


class FakeCrm(TaskMirror):
    name = "fake-crm"

    def __init__(self, enabled=True):
        self.enabled, self.created = enabled, []

    def is_enabled(self):
        return self.enabled

    async def create_task(self, task, project):
        self.created.append(task.title)
        task.salesforce_task_id = "00T1"
        return DispatchResult(self.name, True, external_id="00T1", external_url="https://x/00T1")


def session_with(rows):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


def transcript_and_result():
    return MagicMock(id=uuid.uuid4()), MagicMock()


async def test_dispatcher_mirrors_to_crm_even_when_a_tracker_issue_exists():
    tracked = task(linear_issue_id="issue")
    untracked = task(title="Another")
    crm = FakeCrm()
    session = session_with([tracked, untracked])
    transcript, extraction = transcript_and_result()
    outcomes = await IntegrationDispatcher(crm_mirrors=[crm]).publish(
        session, transcript, None, extraction
    )
    assert crm.created == [tracked.title, "Another"]
    assert [o.adapter for o in outcomes] == ["fake-crm", "fake-crm"]
    kinds = [c.args[0].kind for c in session.add.call_args_list]
    assert kinds == ["external_mirror_created"] * 2


async def test_dispatcher_skips_recorded_and_disabled_crm():
    done = task(salesforce_task_id="00T9")
    crm = FakeCrm()
    transcript, extraction = transcript_and_result()
    await IntegrationDispatcher(crm_mirrors=[crm]).publish(
        session_with([done]), transcript, None, extraction
    )
    assert crm.created == []
    disabled = FakeCrm(enabled=False)
    await IntegrationDispatcher(crm_mirrors=[disabled]).publish(
        session_with([task()]), transcript, None, extraction
    )
    assert disabled.created == []


async def test_dispatcher_survives_a_crm_adapter_crash():
    class Boom(FakeCrm):
        async def create_task(self, task, project):
            raise RuntimeError("down")

    row = task()
    transcript, extraction = transcript_and_result()
    outcomes = await IntegrationDispatcher(crm_mirrors=[Boom()]).publish(
        session_with([row]), transcript, None, extraction
    )
    assert [o.success for o in outcomes] == [False]
