"""Salesforce CRM integration via the REST API.

We create one Salesforce Task record per extracted action item and store its
id and Lightning URL on the task row so the dispatcher never creates it twice.
Authentication is a server-side OAuth refresh-token grant from a Connected
App: the refresh token is exchanged for a short-lived access token, which is
cached in memory and refreshed once if Salesforce answers 401.

Delivery is at least once. Transport failures, HTTP 429 and 5xx are retried
with the same exponential backoff as the other adapters, so a response lost
after Salesforce already saved the record can produce a duplicate Task.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.integrations.base import DispatchResult, TaskMirror
from app.logging import get_logger
from app.models.project import Project
from app.models.task import Task

logger = get_logger("app.integrations.salesforce")

_LOGIN_HOSTS = {False: "https://login.salesforce.com", True: "https://test.salesforce.com"}
# Salesforce caps Task.Subject at 255 characters and Task.Description at 32,000.
_SUBJECT_MAX = 255
_DESCRIPTION_MAX = 32_000
_PRIORITY = {"urgent": "High", "high": "High", "medium": "Normal", "low": "Low"}


class SalesforceError(RuntimeError):
    """A non-retryable Salesforce failure. Never carries credentials or response bodies."""


class _Transient(Exception):
    """A failure worth retrying: network error, HTTP 429, or HTTP 5xx."""


class SalesforceAdapter(TaskMirror):
    name = "salesforce"

    def __init__(self) -> None:
        s = get_settings()
        self._instance = s.salesforce_instance_url.rstrip("/")
        self._client_id = s.salesforce_client_id
        self._client_secret = s.salesforce_client_secret.get_secret_value()
        self._refresh_token = s.salesforce_refresh_token.get_secret_value()
        self._login_url = _LOGIN_HOSTS[s.salesforce_sandbox]
        self._version = s.salesforce_api_version
        self._access_token: str | None = None

    def is_enabled(self) -> bool:
        return get_settings().salesforce_enabled

    async def create_task(self, task: Task, project: Project | None) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        if task.salesforce_task_id:
            return DispatchResult(self.name, True, detail="exists")

        record = {
            "Subject": task.title[:_SUBJECT_MAX],
            "Description": _format_description(task, project)[:_DESCRIPTION_MAX],
            "Status": "Not Started",
            "Priority": _PRIORITY.get(task.priority, "Normal"),
        }
        if task.due_date is not None:
            record["ActivityDate"] = task.due_date.date().isoformat()

        try:
            body = await self._create_record(record)
        except Exception as exc:
            logger.warning("salesforce.create_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))

        record_id = body.get("id")
        if not body.get("success") or not record_id:
            return DispatchResult(self.name, False, detail="Salesforce reported failure")
        url = f"{self._instance}/lightning/r/Task/{record_id}/view"
        task.salesforce_task_id = record_id
        task.salesforce_task_url = url
        return DispatchResult(self.name, True, external_id=record_id, external_url=url)

    async def _create_record(self, record: dict) -> dict:
        path = f"{self._instance}/services/data/{self._version}/sobjects/Task"
        for attempt in (0, 1):
            token = await self._get_access_token(force=attempt == 1)
            response = await self._request(
                "POST", path, headers={"Authorization": f"Bearer {token}"}, json=record
            )
            # A cached token may have expired or been revoked: refresh once, then retry.
            if response.status_code == 401 and attempt == 0:
                continue
            break
        if response.status_code != 201:
            raise SalesforceError(f"Salesforce create failed ({_error_summary(response)})")
        return response.json()

    async def _get_access_token(self, *, force: bool = False) -> str:
        if self._access_token and not force:
            return self._access_token
        response = await self._request(
            "POST",
            f"{self._login_url}/services/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            },
        )
        if response.status_code != 200:
            raise SalesforceError(f"Salesforce authentication failed ({_error_summary(response)})")
        token = response.json().get("access_token")
        if not token:
            raise SalesforceError("Salesforce authentication returned no access token")
        self._access_token = token
        return token

    @retry(
        reraise=True,
        retry=retry_if_exception_type(_Transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
    )
    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise _Transient(type(exc).__name__) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise _Transient(f"HTTP {response.status_code}")
        return response


def _error_summary(response: httpx.Response) -> str:
    """Status plus Salesforce's error code only, never the message or request data."""
    code = ""
    try:
        body = response.json()
        first = body[0] if isinstance(body, list) and body else body
        if isinstance(first, dict):
            code = str(first.get("errorCode") or first.get("error") or "")[:64]
    except ValueError:
        pass
    return f"HTTP {response.status_code} {code}".strip()


def _format_description(task: Task, project: Project | None) -> str:
    parts: list[str] = []
    if task.description:
        parts.append(task.description)
    parts.append("Source quote:")
    parts.append(f'"{task.source_quote.strip()}"' if task.source_quote else "(no quote)")
    if task.owner:
        parts.append(f"Inferred owner: {task.owner}")
    if project:
        parts.append(f"Project: {project.name}")
    parts.append("Auto-created by the Autonomous Operational Intelligence Layer.")
    return "\n\n".join(parts)
