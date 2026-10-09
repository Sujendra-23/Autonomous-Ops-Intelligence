"""Inbound field-service webhook: HMAC verification (pure) and event handling (Postgres)."""

import json
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api import field_service_webhook as webhook_api
from app.config import Settings
from app.database import get_session
from app.integrations.field_service_webhook import REPLAY_WINDOW_SECONDS, verify_signature
from app.models.task import Task, TaskActivity
from mock_field_service.server import sign

SECRET = "s" * 40
NOW = 1_800_000_000


def body_bytes(**event) -> bytes:
    event = {"id": "evt-1", "type": "job.completed", "data": {"jobId": 777}, **event}
    return json.dumps(event).encode()


# ------------------------- signature verification (pure) ------------------ #


def test_valid_signature_from_the_mock_servers_signer_is_accepted():
    body = body_bytes()
    assert verify_signature(SECRET, body, str(NOW), sign(SECRET, body, NOW), now=NOW)


@pytest.mark.parametrize(
    "case",
    [
        "wrong-secret",
        "tampered-body",
        "stale",
        "future",
        "no-signature",
        "no-timestamp",
        "bad-timestamp",
        "no-prefix",
        "empty-secret",
    ],
)
def test_signature_rejections(case):
    body, ts, secret = body_bytes(), NOW, SECRET
    signature = sign(SECRET, body, ts)
    now = NOW
    if case == "wrong-secret":
        secret = "x" * 40
    elif case == "tampered-body":
        body = body_bytes(id="evt-2")
    elif case == "stale":
        now = NOW + REPLAY_WINDOW_SECONDS + 1
    elif case == "future":
        now = NOW - REPLAY_WINDOW_SECONDS - 1
    elif case == "no-signature":
        signature = None
    elif case == "no-timestamp":
        ts = None
    elif case == "bad-timestamp":
        ts = "yesterday"
    elif case == "no-prefix":
        signature = signature.removeprefix("sha256=")
    elif case == "empty-secret":
        secret = ""
    assert not verify_signature(secret, body, None if ts is None else str(ts), signature, now=now)


def test_timestamp_is_bound_into_the_signature():
    body = body_bytes()
    signature = sign(SECRET, body, NOW)
    assert not verify_signature(SECRET, body, str(NOW + 1), signature, now=NOW)


# ------------------------------- HTTP route ------------------------------- #


def webhook_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None, field_service_webhook_secret=overrides.pop("secret", SECRET), **overrides
    )


@pytest.fixture
def api(db_session, monkeypatch):
    app = FastAPI()
    app.include_router(webhook_api.router, prefix="/webhooks")

    async def session():
        yield db_session

    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(webhook_api, "base_settings", lambda: webhook_settings())
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def post(client, body: bytes, *, secret=SECRET, ts=None, signature=None):
    ts = int(time.time()) if ts is None else ts
    headers = {
        "X-FS-Timestamp": str(ts),
        "X-FS-Signature": signature or sign(secret, body, ts),
        "Content-Type": "application/json",
    }
    return await client.post("/webhooks/field-service", content=body, headers=headers)


async def make_task(db_session, **kwargs) -> Task:
    task = Task(
        id=uuid.uuid4(),
        title="Fix boiler",
        status=kwargs.pop("status", "open"),
        priority="high",
        field_service_job_id=kwargs.pop("job_id", "777"),
        **kwargs,
    )
    db_session.add(task)
    await db_session.commit()
    return task


async def activities(db_session, task):
    rows = await db_session.execute(
        select(TaskActivity).where(
            TaskActivity.task_id == task.id, TaskActivity.kind == "field_service_event"
        )
    )
    return rows.scalars().all()


async def test_completed_event_marks_task_done_once(api, db_session):
    task = await make_task(db_session)
    first = await post(api, body_bytes())
    assert first.status_code == 200 and first.json() == {"status": "applied"}
    await db_session.refresh(task)
    assert task.status == "done"
    (activity,) = await activities(db_session, task)
    assert activity.payload["event_id"] == "evt-1" and activity.payload["to"] == "done"

    replay = await post(api, body_bytes())  # provider retry: same event id
    assert replay.json() == {"status": "duplicate"}
    assert len(await activities(db_session, task)) == 1


async def test_cancel_and_hold_events_and_terminal_states_are_not_overwritten(api, db_session):
    held = await make_task(db_session, job_id="1")
    await post(api, body_bytes(id="a", type="job.hold", data={"jobId": 1}))
    await db_session.refresh(held)
    assert held.status == "blocked"
    await post(api, body_bytes(id="b", type="job.canceled", data={"jobId": 1}))
    await db_session.refresh(held)
    assert held.status == "cancelled"
    await post(api, body_bytes(id="c", type="job.hold", data={"jobId": 1}))  # late, out of order
    await db_session.refresh(held)
    assert held.status == "cancelled"
    assert len(await activities(db_session, held)) == 3  # still audited


async def test_appointment_events_are_recorded_without_changing_status(api, db_session):
    task = await make_task(db_session, status="in_progress")
    response = await post(api, body_bytes(type="appointment.rescheduled"))
    assert response.json() == {"status": "applied"}
    await db_session.refresh(task)
    assert task.status == "in_progress"
    (activity,) = await activities(db_session, task)
    assert activity.payload["type"] == "appointment.rescheduled" and activity.payload["to"] is None


async def test_unknown_job_and_malformed_events_are_acknowledged_not_retried(api, db_session):
    assert (await post(api, body_bytes(data={"jobId": 5}))).json() == {"status": "unknown_job"}
    assert (await post(api, json.dumps({"type": "job.completed"}).encode())).json() == {
        "status": "ignored"
    }


async def test_route_rejections(api, db_session, monkeypatch):
    task = await make_task(db_session)
    body = body_bytes()
    assert (await post(api, body, secret="x" * 40)).status_code == 401
    assert (await post(api, body, ts=int(time.time()) - 3600)).status_code == 401
    missing = await api.post("/webhooks/field-service", content=body)
    assert missing.status_code == 401
    assert (await post(api, b"not json")).status_code == 400
    assert (await post(api, b"[1,2]")).status_code == 400
    assert (await post(api, b"x" * 1_000_001)).status_code == 413
    await db_session.refresh(task)
    assert task.status == "open"

    monkeypatch.setattr(webhook_api, "base_settings", lambda: webhook_settings(secret=""))
    assert (await post(api, body)).status_code == 503  # unconfigured fails closed
    oidc = webhook_settings().model_copy(update={"auth_mode": "oidc"})
    monkeypatch.setattr(webhook_api, "base_settings", lambda: oidc)
    assert (await post(api, body)).status_code == 503  # multi-tenant routing not implemented


async def test_status_change_enqueues_task_updated_webhook_when_configured(
    api, db_session, monkeypatch
):
    from app.integrations import webhooks
    from app.models.integration import WebhookDelivery

    outbound = Settings(
        _env_file=None, webhook_url="https://hooks.example.com/aoi", webhook_secret="w" * 32
    )
    monkeypatch.setattr(webhooks, "get_settings", lambda: outbound)
    task = await make_task(db_session)
    await post(api, body_bytes())
    row = (await db_session.execute(select(WebhookDelivery))).scalars().one()
    assert row.event_type == "task.updated"
    assert row.payload["data"]["id"] == str(task.id)
    assert row.payload["data"]["source"] == "field_service"
