"""Mock field-service server used to develop and test the connector offline.

It imitates the *shape* of a ServiceTitan-style API: an OAuth2 client-credentials token endpoint,
tenant-scoped Jobs and Appointments endpoints guarded by a bearer token and an app-key header, and
signed outbound webhooks. It is **not** ServiceTitan and has not been checked against ServiceTitan's
real schema or behaviour; it only exists so the AOI connector has something concrete to talk to.

Behaviours worth testing against (all in-memory, one process):

* Tokens expire (`token_ttl` seconds) and unknown/expired tokens get 401.
* `Idempotency-Key` on POST: a replay with the same key and body returns the original record with
  `Idempotent-Replayed: true`; the same key with a different body gets 422.
* Fault injection via `POST /_mock/faults`: fail the next N requests with a status code, or commit
  the write and *then* fail ("lost response"), which is what makes idempotency keys matter.
* `POST /_mock/events` signs and delivers a webhook event to a target URL.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

REQUIRED_JOB_FIELDS = (
    "customerId",
    "locationId",
    "businessUnitId",
    "jobTypeId",
    "priority",
    "summary",
)
PRIORITIES = {"Low", "Normal", "High", "Urgent"}


@dataclass
class MockState:
    client_id: str = "mock-client"
    client_secret: str = "mock-secret"
    app_key: str = "mock-app-key"
    webhook_secret: str = "w" * 32
    token_ttl: int = 3600
    clock: Callable[[], float] = time.time
    tokens: dict[str, float] = field(default_factory=dict)
    jobs: dict[int, dict] = field(default_factory=dict)
    appointments: dict[int, dict] = field(default_factory=dict)
    idempotency: dict[str, tuple[str, dict]] = field(default_factory=dict)
    token_requests: int = 0
    # Fault injection
    fail_next: int = 0
    fail_status: int = 503
    fail_after_commit: bool = False
    retry_after: int = 2
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1000))

    def next_id(self) -> int:
        return next(self._ids)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def create_app(state: MockState | None = None) -> FastAPI:
    state = state or MockState()
    app = FastAPI(title="Mock field-service API (not ServiceTitan)")
    app.state.mock = state

    def authenticate(authorization: str | None, app_key: str | None) -> None:
        token = (authorization or "").removeprefix("Bearer ").strip()
        expires = state.tokens.get(token)
        if expires is None or expires <= state.clock():
            raise HTTPException(401, "invalid or expired token")
        if not secrets.compare_digest(app_key or "", state.app_key):
            raise HTTPException(403, "invalid app key")

    def retry_headers() -> dict[str, str] | None:
        return {"Retry-After": str(state.retry_after)} if state.fail_status == 429 else None

    def maybe_fail() -> None:
        if state.fail_next > 0 and not state.fail_after_commit:
            state.fail_next -= 1
            raise HTTPException(state.fail_status, "injected failure", headers=retry_headers())

    def commit_then_maybe_fail() -> None:
        if state.fail_next > 0 and state.fail_after_commit:
            state.fail_next -= 1
            raise HTTPException(
                state.fail_status, "injected failure after commit", headers=retry_headers()
            )

    @app.post("/connect/token")
    async def token(request: Request):
        form = await request.form()
        state.token_requests += 1
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        if form.get("client_id") != state.client_id or not secrets.compare_digest(
            str(form.get("client_secret", "")), state.client_secret
        ):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        value = secrets.token_urlsafe(24)
        state.tokens[value] = state.clock() + state.token_ttl
        return {"access_token": value, "token_type": "Bearer", "expires_in": state.token_ttl}

    async def idempotent_create(
        request: Request, store: dict[int, dict], build: Callable[[dict], dict], key: str | None
    ):
        body = await request.json()
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        scope = f"{request.url.path}:{key}" if key else None
        if scope and scope in state.idempotency:
            stored_fingerprint, record = state.idempotency[scope]
            if stored_fingerprint != fingerprint:
                raise HTTPException(422, "Idempotency-Key reused with a different request body")
            return JSONResponse(record, status_code=200, headers={"Idempotent-Replayed": "true"})
        record = build(body)
        store[record["id"]] = record
        if scope:
            state.idempotency[scope] = (fingerprint, record)
        commit_then_maybe_fail()
        return JSONResponse(record, status_code=201)

    def build_appointment(body: dict, job_id: int) -> dict:
        return {
            "id": state.next_id(),
            "jobId": job_id,
            "start": body["start"],
            "end": body["end"],
            "status": "Scheduled",
            "technicianIds": body.get("technicianIds", []),
        }

    @app.post("/jpm/v2/tenant/{tenant}/jobs")
    async def create_job(
        tenant: str,
        request: Request,
        authorization: str | None = Header(None),
        st_app_key: str | None = Header(None),
        idempotency_key: str | None = Header(None),
    ):
        authenticate(authorization, st_app_key)
        maybe_fail()

        def build(body: dict) -> dict:
            missing = [f for f in REQUIRED_JOB_FIELDS if body.get(f) in (None, "")]
            if missing:
                raise HTTPException(422, f"missing required fields: {', '.join(missing)}")
            if body["priority"] not in PRIORITIES:
                raise HTTPException(422, "priority must be one of Low, Normal, High, Urgent")
            job_id = state.next_id()
            items = body.get("appointments") or []
            if any(not i.get("start") or not i.get("end") for i in items):
                raise HTTPException(422, "appointment needs start and end")
            appointments = []
            for item in items:
                appointment = build_appointment(item, job_id)
                state.appointments[appointment["id"]] = appointment
                appointments.append(appointment)
            return {
                "id": job_id,
                "tenant": tenant,
                "jobNumber": f"J-{job_id}",
                "status": "Scheduled" if appointments else "Open",
                "summary": body["summary"],
                "priority": body["priority"],
                "customerId": body["customerId"],
                "locationId": body["locationId"],
                "externalData": body.get("externalData", []),
                "appointments": appointments,
            }

        return await idempotent_create(request, state.jobs, build, idempotency_key)

    @app.get("/jpm/v2/tenant/{tenant}/jobs/{job_id}")
    async def get_job(
        tenant: str,
        job_id: int,
        authorization: str | None = Header(None),
        st_app_key: str | None = Header(None),
    ):
        authenticate(authorization, st_app_key)
        if job_id not in state.jobs:
            raise HTTPException(404, "job not found")
        return state.jobs[job_id]

    @app.post("/jpm/v2/tenant/{tenant}/appointments")
    async def create_appointment(
        tenant: str,
        request: Request,
        authorization: str | None = Header(None),
        st_app_key: str | None = Header(None),
        idempotency_key: str | None = Header(None),
    ):
        authenticate(authorization, st_app_key)
        maybe_fail()

        def build(body: dict) -> dict:
            job_id = body.get("jobId")
            if job_id not in state.jobs:
                raise HTTPException(422, "unknown jobId")
            if not body.get("start") or not body.get("end"):
                raise HTTPException(422, "appointment needs start and end")
            appointment = build_appointment(body, job_id)
            state.jobs[job_id]["appointments"].append(appointment)
            return appointment

        return await idempotent_create(request, state.appointments, build, idempotency_key)

    @app.post("/_mock/faults")
    async def set_faults(request: Request):
        body = await request.json()
        state.fail_next = int(body.get("fail_next", 0))
        state.fail_status = int(body.get("status", 503))
        state.fail_after_commit = bool(body.get("after_commit", False))
        return {"ok": True}

    @app.post("/_mock/events")
    async def emit_event(request: Request):
        """Sign and deliver a webhook event: {type, job_id, target_url, event_id?, timestamp?}."""
        body = await request.json()
        timestamp = int(body.get("timestamp") or state.clock())
        payload = json.dumps(
            {
                "id": body.get("event_id") or secrets.token_hex(8),
                "type": body["type"],
                "occurredOn": timestamp,
                "data": {"jobId": body["job_id"], **body.get("data", {})},
            }
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-FS-Timestamp": str(timestamp),
            "X-FS-Signature": sign(state.webhook_secret, payload, timestamp),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(body["target_url"], content=payload, headers=headers)
        return {"delivered_status": response.status_code}

    return app
