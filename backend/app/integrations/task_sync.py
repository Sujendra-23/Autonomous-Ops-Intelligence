"""Two-way status reconciliation for existing Linear and Jira mirrors.

Pending local changes win until successfully pushed. Otherwise the provider is
authoritative. A task row lock serializes local edits with reconciliation.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.webhooks import enqueue_event, task_data
from app.models.task import Task, TaskActivity


class SyncError(Exception):
    """A safe, actionable error suitable for the API (no provider response body)."""


def mark_sync_pending(task: Task) -> None:
    if get_settings().task_sync_enabled and (task.linear_issue_id or task.jira_issue_key):
        task.sync_pending = True
        task.sync_error = None
        task.sync_checked_at = None


def mapped_status(state: dict, mapping: dict[str, str], *, linear: bool) -> str:
    inverse = {remote: local for local, remote in mapping.items()}
    if str(state.get("id")) in inverse:
        return inverse[str(state["id"])]
    category = state.get("type") if linear else state.get("statusCategory", {}).get("key")
    defaults = (
        {
            "backlog": "open",
            "unstarted": "open",
            "started": "in_progress",
            "completed": "done",
            "canceled": "cancelled",
        }
        if linear
        else {"new": "open", "indeterminate": "in_progress", "done": "done"}
    )
    if category not in defaults:
        raise SyncError("Unknown provider status; configure the status map")
    return defaults[category]


class StatusClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.settings = get_settings()

    async def gql(self, query: str, variables: dict) -> dict:
        response = await self.client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": self.settings.linear_api_key.get_secret_value()},
            json={"query": query, "variables": variables},
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors") or not body.get("data"):
            raise SyncError("Linear rejected the request; check credentials and issue access")
        return body["data"]

    async def linear(self, task: Task) -> str:
        if not self.settings.linear_enabled:
            raise SyncError("Linear is not configured")
        data = await self.gql(
            """query($id: String!) {
          issue(id: $id) { state { id type } team { states { nodes { id type } } } }
        }""",
            {"id": task.linear_issue_id},
        )
        issue = data.get("issue")
        if not issue:
            raise SyncError("Linear issue is unavailable")
        mapping = self.settings.linear_status_map
        current = mapped_status(issue["state"], mapping, linear=True)
        if not task.sync_pending or current == task.status:
            return current
        state_id = mapping.get(task.status)
        if not state_id:
            category = {
                "open": "unstarted",
                "in_progress": "started",
                "done": "completed",
                "cancelled": "canceled",
            }.get(task.status)
            candidates = [
                s["id"]
                for s in issue["team"]["states"]["nodes"]
                if s["type"] == category and mapped_status(s, mapping, linear=True) == task.status
            ]
            if len(candidates) != 1:
                raise SyncError("Set LINEAR_STATUS_MAP for this ambiguous or custom status")
            state_id = candidates[0]
        result = await self.gql(
            """mutation($id: String!, $state: String!) {
          issueUpdate(id: $id, input: {stateId: $state}) { success }
        }""",
            {"id": task.linear_issue_id, "state": state_id},
        )
        if not result.get("issueUpdate", {}).get("success"):
            raise SyncError("Linear did not accept the status change")
        return task.status

    async def jira_request(self, method: str, path: str, **kwargs) -> dict:
        response = await self.client.request(
            method,
            self.settings.jira_base_url.rstrip("/") + path,
            auth=(self.settings.jira_email, self.settings.jira_api_token.get_secret_value()),
            headers={"Accept": "application/json"},
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    async def jira(self, task: Task) -> str:
        if not self.settings.jira_enabled:
            raise SyncError("Jira is not configured")
        path = "/rest/api/3/issue/" + quote(task.jira_issue_key, safe="")
        issue = await self.jira_request("GET", path, params={"fields": "status"})
        mapping = self.settings.jira_status_map
        current = mapped_status(issue["fields"]["status"], mapping, linear=False)
        if not task.sync_pending or current == task.status:
            return current
        data = await self.jira_request("GET", path + "/transitions")
        target_id = mapping.get(task.status)
        candidates = [
            t
            for t in data.get("transitions", [])
            if (
                str(t["to"]["id"]) == target_id
                if target_id
                else task.status in ("open", "in_progress", "done")
                and mapped_status(t["to"], mapping, linear=False) == task.status
            )
        ]
        if len(candidates) != 1:
            raise SyncError("Set JIRA_STATUS_MAP or make a unique workflow transition available")
        await self.jira_request(
            "POST", path + "/transitions", json={"transition": {"id": candidates[0]["id"]}}
        )
        return task.status


async def sync_task_statuses(session: AsyncSession, limit: int = 100) -> int:
    settings = get_settings()
    if not settings.task_sync_enabled:
        return 0
    updated = 0
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.integration_interval_seconds)
    for _ in range(limit):
        task = await session.scalar(
            select(Task)
            .where(
                or_(Task.linear_issue_id.is_not(None), Task.jira_issue_key.is_not(None)),
                or_(Task.sync_checked_at.is_(None), Task.sync_checked_at <= cutoff),
            )
            .order_by(Task.sync_checked_at.asc().nullsfirst(), Task.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            await session.commit()
            break
        provider = "linear" if task.linear_issue_id else "jira"
        pending = task.sync_pending
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                adapter = StatusClient(client)
                status = await getattr(adapter, provider)(task)
            if status != task.status:
                previous = task.status
                task.status = status
                task.last_status_change_at = datetime.now(UTC)
                session.add(
                    TaskActivity(
                        task_id=task.id,
                        kind="external_status_change",
                        actor=provider,
                        payload={"from": previous, "to": status},
                    )
                )
                enqueue_event(session, "task.updated", {**task_data(task), "source": provider})
                updated += 1
            elif pending:
                session.add(
                    TaskActivity(
                        task_id=task.id,
                        kind="external_status_pushed",
                        actor=provider,
                        payload={"status": status},
                    )
                )
            task.sync_pending = False
            task.sync_error = None
        except (httpx.HTTPError, SyncError, KeyError, ValueError) as exc:
            task.sync_error = (
                str(exc)
                if isinstance(exc, SyncError)
                else "Provider request failed; check credentials and workflow permissions"
            )
        task.sync_checked_at = datetime.now(UTC)
        await session.commit()
    return updated
