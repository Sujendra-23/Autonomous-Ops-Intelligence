"""Field-service connector against the in-process mock server (no sockets, no real provider)."""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.integrations import field_service
from app.integrations.base import DispatchResult
from app.integrations.dispatcher import IntegrationDispatcher
from app.integrations.field_service import (
    MAX_ATTEMPTS,
    FieldServiceAdapter,
    FieldServiceClient,
    FieldServiceError,
    idempotency_key_for_task,
    job_payload,
)
from app.models.project import Project
from app.models.task import Task
from mock_field_service.server import MockState, create_app

BASE = "http://localhost:9100"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def mock():
    state = MockState(clock=Clock())
    return state, create_app(state)


def make_settings(**overrides) -> Settings:
    values = {
        "field_service_base_url": BASE,
        "field_service_auth_url": BASE,
        "field_service_tenant_id": "tenant-1",
        "field_service_client_id": "mock-client",
        "field_service_client_secret": SecretStr("mock-secret"),
        "field_service_app_key": SecretStr("mock-app-key"),
        "field_service_customer_id": 11,
        "field_service_location_id": 22,
        "field_service_business_unit_id": 33,
        "field_service_job_type_id": 44,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.fixture
def settings(monkeypatch):
    config = make_settings()
    monkeypatch.setattr(field_service, "get_settings", lambda: config)
    return config


class Harness:
    """A client wired to the mock app with a fake clock and recorded sleeps."""

    def __init__(self, state, app, settings, transport=None):
        self.state, self.app = state, app
        self.sleeps: list[float] = []
        self.requests: list[httpx.Request] = []
        asgi = httpx.ASGITransport(app=app)
        self.transport = transport(asgi) if transport else asgi
        self.client = FieldServiceClient(
            settings,
            transport=Recorder(self.transport, self.requests),
            clock=state.clock,
            sleep=self._sleep,
        )

    async def _sleep(self, seconds):
        self.sleeps.append(seconds)

    def faults(self, **body):
        self.state.fail_next = body.get("fail_next", 0)
        self.state.fail_status = body.get("status", 503)
        self.state.fail_after_commit = body.get("after_commit", False)

    @property
    def job_posts(self):
        return [r for r in self.requests if r.url.path.endswith("/jobs") and r.method == "POST"]


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self, inner, log):
        self.inner, self.log = inner, log

    async def handle_async_request(self, request):
        self.log.append(request)
        return await self.inner.handle_async_request(request)


@pytest.fixture
def harness(mock, settings):
    state, app = mock
    return Harness(state, app, settings)


PAYLOAD = {
    "customerId": 11,
    "locationId": 22,
    "businessUnitId": 33,
    "jobTypeId": 44,
    "priority": "High",
    "summary": "Fix the boiler",
}


# ------------------------------- OAuth ----------------------------------- #


async def test_token_is_cached_across_requests(harness):
    for i in range(3):
        await harness.client.create_job(PAYLOAD, idempotency_key=f"k{i}")
    assert harness.state.token_requests == 1
    assert len(harness.state.jobs) == 3


async def test_token_refreshes_before_expiry(harness):
    await harness.client.create_job(PAYLOAD, idempotency_key="a")
    harness.state.clock.now += 3600 - 61  # still valid, outside the skew window
    await harness.client.create_job(PAYLOAD, idempotency_key="b")
    assert harness.state.token_requests == 1
    harness.state.clock.now += 2  # now inside the 60 s skew: refresh proactively
    await harness.client.create_job(PAYLOAD, idempotency_key="c")
    assert harness.state.token_requests == 2


async def test_revoked_token_is_refreshed_once_on_401(harness):
    await harness.client.create_job(PAYLOAD, idempotency_key="a")
    harness.state.tokens.clear()  # server forgets every token
    job = await harness.client.create_job(PAYLOAD, idempotency_key="b")
    assert job["id"] and harness.state.token_requests == 2
    assert harness.sleeps == []  # re-auth is not a backoff retry


async def test_bad_credentials_fail_fast_without_leaking_secrets(mock):
    state, app = mock
    bad = make_settings(field_service_client_secret=SecretStr("wrong-secret-value"))
    client = FieldServiceClient(bad, transport=httpx.ASGITransport(app=app), clock=state.clock)
    with pytest.raises(FieldServiceError) as err:
        await client.create_job(PAYLOAD, idempotency_key="a")
    assert "401" in str(err.value) and "wrong-secret-value" not in str(err.value)
    assert state.token_requests == 1 and not state.jobs


async def test_wrong_app_key_is_a_clear_non_retried_error(mock):
    state, app = mock
    client = FieldServiceClient(
        make_settings(field_service_app_key=SecretStr("nope")),
        transport=httpx.ASGITransport(app=app),
        clock=state.clock,
    )
    with pytest.raises(FieldServiceError, match="403"):
        await client.create_job(PAYLOAD, idempotency_key="a")


# --------------------------- idempotency / retries ------------------------ #


async def test_same_key_twice_creates_one_job(harness):
    first = await harness.client.create_job(PAYLOAD, idempotency_key="same")
    second = await harness.client.create_job(PAYLOAD, idempotency_key="same")
    assert first["id"] == second["id"] and len(harness.state.jobs) == 1


async def test_key_reuse_with_different_body_is_rejected(harness):
    await harness.client.create_job(PAYLOAD, idempotency_key="same")
    with pytest.raises(FieldServiceError, match="422"):
        await harness.client.create_job({**PAYLOAD, "summary": "Different"}, idempotency_key="same")
    assert len(harness.state.jobs) == 1


async def test_lost_response_is_retried_with_same_key_and_creates_one_job(harness):
    harness.faults(fail_next=1, status=503, after_commit=True)  # job saved, reply lost
    job = await harness.client.create_job(PAYLOAD, idempotency_key="aoi-1")
    assert len(harness.state.jobs) == 1 and job["id"] in harness.state.jobs
    posts = harness.job_posts
    assert len(posts) == 2
    assert {p.headers["Idempotency-Key"] for p in posts} == {"aoi-1"}
    assert harness.sleeps == [0.5]


async def test_transient_failures_back_off_exponentially_then_succeed(harness):
    harness.faults(fail_next=2, status=500)
    await harness.client.create_job(PAYLOAD, idempotency_key="aoi-2")
    assert harness.sleeps == [0.5, 1.0] and len(harness.state.jobs) == 1
    assert len(harness.job_posts) == 3


async def test_retry_after_header_is_honoured_on_429(harness):
    harness.faults(fail_next=1, status=429)
    await harness.client.create_job(PAYLOAD, idempotency_key="aoi-3")
    assert harness.sleeps == [2.0]


async def test_gives_up_after_max_attempts_without_creating_anything(harness):
    harness.faults(fail_next=99, status=503)
    with pytest.raises(FieldServiceError, match="gave up"):
        await harness.client.create_job(PAYLOAD, idempotency_key="aoi-4")
    assert len(harness.job_posts) == MAX_ATTEMPTS and not harness.state.jobs


async def test_validation_errors_are_not_retried(harness):
    with pytest.raises(FieldServiceError, match="HTTP 422"):
        await harness.client.create_job({"summary": "x"}, idempotency_key="aoi-5")
    assert len(harness.job_posts) == 1 and harness.sleeps == []


async def test_network_errors_are_retried(mock, settings):
    state, app = mock

    class Flaky(httpx.AsyncBaseTransport):
        def __init__(self, inner):
            self.inner, self.failures = inner, 0

        async def handle_async_request(self, request):
            if request.url.path.endswith("/jobs") and self.failures < 2:
                self.failures += 1
                raise httpx.ConnectError("boom")
            return await self.inner.handle_async_request(request)

    h = Harness(state, app, settings, transport=Flaky)
    await h.client.create_job(PAYLOAD, idempotency_key="aoi-6")
    assert len(state.jobs) == 1 and h.sleeps == [0.5, 1.0]


async def test_appointment_create_is_idempotent_and_needs_a_known_job(harness):
    job = await harness.client.create_job(PAYLOAD, idempotency_key="j")
    start = datetime(2026, 11, 6, 15, tzinfo=UTC)
    end = datetime(2026, 11, 6, 17, tzinfo=UTC)
    a = await harness.client.create_appointment(job["id"], start, end, idempotency_key="a")
    b = await harness.client.create_appointment(job["id"], start, end, idempotency_key="a")
    assert a["id"] == b["id"] and len(harness.state.appointments) == 1
    assert (await harness.client.get_job(job["id"]))["appointments"][0]["id"] == a["id"]
    with pytest.raises(FieldServiceError, match="422"):
        await harness.client.create_appointment(999, start, end, idempotency_key="x")


# --------------------------------- adapter ------------------------------- #


def make_task(**kwargs):
    fields = {"title": "Replace boiler valve", "status": "open", "priority": "urgent"}
    fields.update(kwargs)
    return Task(id=uuid.uuid4(), **fields)


def test_job_payload_maps_priority_appointment_and_marker(settings):
    due = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
    task = make_task(description="Leaking", due_date=due)
    payload = job_payload(task, Project(name="Acme"), settings)
    assert payload["priority"] == "Urgent" and payload["customerId"] == 11
    assert payload["appointments"] == [
        {"start": "2026-11-06T15:00:00+00:00", "end": "2026-11-06T17:00:00+00:00"}
    ]
    assert payload["externalData"] == [{"key": "aoi_task_id", "value": str(task.id)}]
    assert "Leaking" in payload["summary"] and "Project: Acme" in payload["summary"]
    assert "appointments" not in job_payload(make_task(), None, settings)
    assert "campaignId" not in payload
    assert job_payload(task, None, make_settings(field_service_campaign_id=5))["campaignId"] == 5


async def test_adapter_creates_job_and_records_identifiers(harness, settings):
    task = make_task(due_date=datetime(2026, 11, 6, 15, tzinfo=UTC))
    result = await FieldServiceAdapter(harness.client).create_task(task, None)
    job = next(iter(harness.state.jobs.values()))
    assert result.success and result.external_id == str(job["id"])
    assert task.field_service_job_id == str(job["id"])
    assert task.field_service_job_url == f"{BASE}/jobs/{job['id']}"
    assert job["status"] == "Scheduled" and len(job["appointments"]) == 1
    assert harness.job_posts[0].headers["Idempotency-Key"] == idempotency_key_for_task(task.id)


async def test_adapter_skips_when_already_mirrored_or_disabled(harness, settings, monkeypatch):
    adapter = FieldServiceAdapter(harness.client)
    done = make_task(field_service_job_id="123")
    assert (await adapter.create_task(done, None)).detail == "exists"
    monkeypatch.setattr(field_service, "get_settings", lambda: Settings(_env_file=None))
    assert not adapter.is_enabled()
    assert (await adapter.create_task(make_task(), None)).detail == "disabled"
    assert not harness.state.jobs


async def test_crash_after_job_creation_does_not_duplicate_on_redelivery(harness, settings):
    """The DB write of the job id is lost; the redelivered task re-sends the same key."""
    task = make_task()
    adapter = FieldServiceAdapter(harness.client)
    first = await adapter.create_task(task, None)
    task.field_service_job_id = None  # simulate the lost commit
    second = await adapter.create_task(task, None)
    assert first.external_id == second.external_id and len(harness.state.jobs) == 1


async def test_adapter_reports_failure_without_raising(harness, settings):
    harness.faults(fail_next=99, status=503)
    task = make_task()
    result = await FieldServiceAdapter(harness.client).create_task(task, None)
    assert not result.success and "gave up" in result.detail
    assert task.field_service_job_id is None


async def test_dispatcher_creates_field_service_job_even_when_salesforce_already_has_one():
    """Per-mirror 'already mirrored' check: a Salesforce id must not suppress the job."""
    task = make_task(salesforce_task_id="00T000000000001AAA")
    mirror = AsyncMock()
    mirror.is_enabled = lambda: True
    mirror.create_task.return_value = DispatchResult("field_service", True, external_id="9")

    class Session:
        def __init__(self):
            self.added = []

        async def execute(self, *_):
            from unittest.mock import MagicMock

            result = MagicMock()
            result.scalars.return_value.all.return_value = [task]
            return result

        def add(self, row):
            self.added.append(row)

        async def commit(self):
            return None

    disabled = AsyncMock()
    disabled.is_enabled = lambda: False
    dispatcher = IntegrationDispatcher(
        project_mirrors=[disabled],
        task_mirrors=[disabled],
        crm_mirrors=[disabled],
        field_service_mirrors=[mirror],
        notifiers=[],
    )
    transcript = type("T", (), {"id": uuid.uuid4()})()
    session = Session()
    outcomes = await dispatcher.publish(session, transcript, None, None)
    assert [o.adapter for o in outcomes] == ["field_service"]
    assert session.added[0].payload["external_id"] == "9"


# ------------------------------- configuration --------------------------- #


def test_enabled_requires_all_credentials_and_ids():
    assert Settings(_env_file=None).field_service_enabled is False
    assert make_settings().field_service_enabled is True
    for missing in (
        {"field_service_client_secret": SecretStr("")},
        {"field_service_app_key": SecretStr("")},
        {"field_service_tenant_id": ""},
        {"field_service_customer_id": None},
        {"field_service_job_type_id": None},
    ):
        assert make_settings(**missing).field_service_enabled is False


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://user:pw@api.example.com",
        "ftp://x.com",
        "https://api.example.com/?token=1",
    ],
)
def test_urls_must_be_https_without_credentials(url):
    with pytest.raises(ValidationError):
        make_settings(field_service_base_url=url)


def test_loopback_http_allowed_for_the_mock_and_webhook_secret_length_enforced():
    assert make_settings(field_service_base_url="http://127.0.0.1:9100")
    with pytest.raises(ValidationError):
        make_settings(field_service_webhook_secret=SecretStr("short"))
    assert make_settings(field_service_webhook_secret=SecretStr("x" * 32))


def test_secrets_never_appear_in_repr():
    text = repr(make_settings())
    assert "mock-secret" not in text and "mock-app-key" not in text


async def test_error_text_never_contains_provider_bodies(mock, settings):
    state, app = mock

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(400, text="customer Jane Roe SSN 123-45-6789")

    client = FieldServiceClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(FieldServiceError) as err:
        await client.create_job(PAYLOAD, idempotency_key="k")
    assert "Jane" not in str(err.value) and json.dumps(str(err.value)).count("400") == 1
