"""Field-service connector: Jobs and Appointments, ServiceTitan-shaped.

A task becomes a field-service **job** (plus an **appointment** when it has a due date). The
client uses OAuth2 client-credentials with a cached token, sends an ``Idempotency-Key`` on every
create so retries are safe, and retries transport errors, HTTP 429 and 5xx with the *same* key.

Honesty note: this is built and tested against ``backend/mock_field_service``. Real ServiceTitan
needs a partner agreement and an app key; the endpoint paths and field names here follow its
general shape from public documentation, but the connector has never run against ServiceTitan and
its request/response schema is unverified there. Whether a real provider honours
``Idempotency-Key`` is provider-specific; the job also carries an ``externalData`` marker with the
AOI task id so duplicates can be found by hand.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.integrations.base import DispatchResult, TaskMirror
from app.logging import get_logger
from app.models.project import Project
from app.models.task import Task

logger = get_logger("app.integrations.field_service")

_PRIORITY = {"urgent": "Urgent", "high": "High", "medium": "Normal", "low": "Low"}
_SUMMARY_MAX = 1000
TOKEN_SKEW_SECONDS = 60
MAX_ATTEMPTS = 4


class FieldServiceError(RuntimeError):
    """A non-retryable failure. Never carries credentials or response bodies."""


class _Transient(Exception):
    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


def idempotency_key_for_task(task_id: Any, kind: str = "job") -> str:
    """Stable per task, so a retry after a lost response cannot create a second job."""
    return f"aoi-task-{task_id}-{kind}"


class FieldServiceClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        s = settings or get_settings()
        self._base = s.field_service_base_url.rstrip("/")
        self._auth_url = s.field_service_auth_url.rstrip("/")
        self._tenant = s.field_service_tenant_id
        self._client_id = s.field_service_client_id
        self._client_secret = s.field_service_client_secret.get_secret_value()
        self._app_key = s.field_service_app_key.get_secret_value()
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.token_fetches = 0

    # ---- auth ---------------------------------------------------------------------------- #

    async def _access_token(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and self._clock() < self._expires_at - TOKEN_SKEW_SECONDS:
                return self._token
            async with self._http() as client:
                try:
                    response = await client.post(
                        f"{self._auth_url}/connect/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self._client_id,
                            "client_secret": self._client_secret,
                        },
                    )
                except httpx.TransportError as exc:
                    raise _Transient(type(exc).__name__) from exc
            if response.status_code == 429 or response.status_code >= 500:
                raise _Transient(f"auth HTTP {response.status_code}")
            if response.status_code != 200:
                raise FieldServiceError(f"authentication failed (HTTP {response.status_code})")
            try:
                body = response.json()
                token, ttl = body["access_token"], float(body.get("expires_in", 300))
            except (ValueError, KeyError, TypeError) as exc:
                raise FieldServiceError("authentication returned no usable token") from exc
            self.token_fetches += 1
            self._token, self._expires_at = token, self._clock() + ttl
            return token

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20.0, follow_redirects=False, transport=self._transport)

    # ---- requests ------------------------------------------------------------------------ #

    async def _send(
        self, method: str, path: str, *, json: dict | None = None, key: str | None = None
    ) -> dict:
        """One logical request: re-auth once on 401, retry transient failures with the same key."""
        last: _Transient | None = None
        reauthed = False
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                delay = last.retry_after if last and last.retry_after else 0.5 * 2 ** (attempt - 1)
                await self._sleep(min(delay, 30.0))
            try:
                response = await self._once(method, path, json, key, force_token=False)
                if response.status_code == 401 and not reauthed:
                    reauthed = True
                    response = await self._once(method, path, json, key, force_token=True)
            except _Transient as exc:
                last = exc
                logger.warning("field_service.retry", attempt=attempt + 1, reason=str(exc))
                continue
            if response.status_code in (200, 201):
                return response.json()
            if response.status_code == 429 or response.status_code >= 500:
                last = _Transient(f"HTTP {response.status_code}", _retry_after(response))
                logger.warning("field_service.retry", attempt=attempt + 1, reason=str(last))
                continue
            raise FieldServiceError(_summary(response))
        raise FieldServiceError(f"gave up after {MAX_ATTEMPTS} attempts ({last})")

    async def _once(self, method, path, json, key, *, force_token) -> httpx.Response:
        token = await self._access_token(force=force_token)
        headers = {"Authorization": f"Bearer {token}", "ST-App-Key": self._app_key}
        if key:
            headers["Idempotency-Key"] = key
        try:
            async with self._http() as client:
                return await client.request(
                    method,
                    f"{self._base}/jpm/v2/tenant/{self._tenant}{path}",
                    headers=headers,
                    json=json,
                )
        except httpx.TransportError as exc:
            raise _Transient(type(exc).__name__) from exc

    # ---- public API ---------------------------------------------------------------------- #

    async def create_job(self, payload: dict, *, idempotency_key: str) -> dict:
        return await self._send("POST", "/jobs", json=payload, key=idempotency_key)

    async def get_job(self, job_id: str | int) -> dict:
        return await self._send("GET", f"/jobs/{job_id}")

    async def create_appointment(
        self, job_id: str | int, start: datetime, end: datetime, *, idempotency_key: str
    ) -> dict:
        body = {"jobId": job_id, "start": start.isoformat(), "end": end.isoformat()}
        return await self._send("POST", "/appointments", json=body, key=idempotency_key)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def _summary(response: httpx.Response) -> str:
    """Status only: provider bodies can echo customer data."""
    return f"field-service request rejected (HTTP {response.status_code})"


# --------------------------------------------------------------------------- #
# Task -> job mapping and the TaskMirror adapter                               #
# --------------------------------------------------------------------------- #


def job_payload(task: Task, project: Project | None, s: Settings) -> dict:
    summary = task.title
    if task.description:
        summary += f"\n\n{task.description}"
    if project:
        summary += f"\n\nProject: {project.name}"
    payload: dict[str, Any] = {
        "customerId": s.field_service_customer_id,
        "locationId": s.field_service_location_id,
        "businessUnitId": s.field_service_business_unit_id,
        "jobTypeId": s.field_service_job_type_id,
        "priority": _PRIORITY.get(task.priority, "Normal"),
        "summary": summary[:_SUMMARY_MAX],
        "externalData": [{"key": "aoi_task_id", "value": str(task.id)}],
    }
    if s.field_service_campaign_id:
        payload["campaignId"] = s.field_service_campaign_id
    if task.due_date is not None:
        start = task.due_date if task.due_date.tzinfo else task.due_date.replace(tzinfo=UTC)
        end = start + timedelta(minutes=s.field_service_appointment_minutes)
        payload["appointments"] = [{"start": start.isoformat(), "end": end.isoformat()}]
    return payload


class FieldServiceAdapter(TaskMirror):
    name = "field_service"

    def __init__(self, client: FieldServiceClient | None = None) -> None:
        self._client = client

    def is_enabled(self) -> bool:
        return get_settings().field_service_enabled

    def _get_client(self) -> FieldServiceClient:
        # One client per adapter keeps the cached token across tasks in a dispatch.
        if self._client is None:
            self._client = FieldServiceClient()
        return self._client

    async def create_task(self, task: Task, project: Project | None) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        if task.field_service_job_id:
            return DispatchResult(self.name, True, detail="exists")
        payload = job_payload(task, project, get_settings())
        try:
            job = await self._get_client().create_job(
                payload, idempotency_key=idempotency_key_for_task(task.id)
            )
        except FieldServiceError as exc:
            logger.warning("field_service.create_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))
        job_id = str(job.get("id") or "")
        if not job_id:
            return DispatchResult(self.name, False, detail="Provider returned no job id")
        url = f"{get_settings().field_service_base_url.rstrip('/')}/jobs/{job_id}"
        task.field_service_job_id = job_id
        task.field_service_job_url = url
        return DispatchResult(self.name, True, external_id=job_id, external_url=url)
