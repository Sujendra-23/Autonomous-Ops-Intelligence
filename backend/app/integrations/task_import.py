"""Read existing Jira/Linear issues into the company's shared task list.

Imports never create remote issues or invoke the LLM. Destination-bound cursors
resume large backfills; provider identities and workspace locks prevent duplicates.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.oauth import OAuthError
from app.integrations.task_sync import StatusClient, SyncError, mapped_status
from app.models.task import Task, TaskActivity, TaskImportState
from app.tenancy import LEGACY_WORKSPACE, workspace_context

PROVIDERS = ("jira", "linear")
PAGE_SIZE = 100
PAGES_PER_PASS = 2


def workspace_id():
    value = workspace_context.get()
    if get_settings().auth_mode == "oidc" and value is None:
        raise SyncError("A company workspace is required to import tasks")
    return value or LEGACY_WORKSPACE


def enabled(provider, settings):
    return bool(
        getattr(settings, provider + "_enabled")
        and (settings.jira_project_key if provider == "jira" else settings.linear_team_id)
    )


def identity_scope(provider, settings):
    site = settings.jira_base_url.rstrip("/").lower() if provider == "jira" else "linear"
    return hashlib.sha256(site.encode()).hexdigest()


def destination_scope(provider, settings):
    destination = settings.jira_project_key if provider == "jira" else settings.linear_team_id
    return hashlib.sha256(
        f"{identity_scope(provider, settings)}:{destination}".encode()
    ).hexdigest()


def date_value(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def adf_text(node):
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    value = node.get("text", "") + "".join(adf_text(child) for child in node.get("content", []))
    return value + (
        "\n" if node.get("type") in {"paragraph", "heading", "listItem", "hardBreak"} else ""
    )


def safe_link(value):
    parsed = urlsplit(value or "")
    return (
        value[:512] if parsed.scheme == "https" and parsed.netloc and not parsed.username else None
    )


@dataclass
class RemoteTask:
    id: str
    title: str
    description: str | None
    owner: str | None
    due_date: datetime | None
    status: str
    priority: str
    url: str | None
    updated_at: str | None
    key: str | None = None


class ImportClient(StatusClient):
    async def page(self, provider: str, cursor: str | None):
        if provider == "jira":
            result = await self.jira_request(
                "POST",
                "/rest/api/3/search/jql",
                json={
                    "jql": (
                        f"project = {json.dumps(self.settings.jira_project_key)} "
                        "ORDER BY created ASC, key ASC"
                    ),
                    "fields": [
                        "summary",
                        "description",
                        "assignee",
                        "duedate",
                        "status",
                        "priority",
                        "updated",
                        "project",
                    ],
                    "maxResults": PAGE_SIZE,
                    **({"nextPageToken": cursor} if cursor else {}),
                },
            )
            issues = result["issues"]
            # Even if a provider returns an unexpected page, never import another project.
            for issue in issues:
                if issue["fields"]["project"]["key"] != self.settings.jira_project_key:
                    raise SyncError("Jira returned issues outside the selected project")
            next_cursor = None if result.get("isLast") else result.get("nextPageToken")
            if result.get("isLast") is False and not next_cursor:
                raise SyncError("Jira pagination failed; retry the import")
        else:
            result = await self.gql(
                """query ImportTasks($team: ID!, $after: String, $first: Int!) {
                issues(first: $first, after: $after, filter: {team: {id: {eq: $team}}}) {
                    nodes { id identifier title description url dueDate updatedAt priority
                        assignee { name } state { id type } team { id } }
                    pageInfo { hasNextPage endCursor }
                }
            }""",
                {"team": self.settings.linear_team_id, "after": cursor, "first": PAGE_SIZE},
            )
            connection = result["issues"]
            issues = connection["nodes"]
            if any(issue["team"]["id"] != self.settings.linear_team_id for issue in issues):
                raise SyncError("Linear returned issues outside the selected team")
            info = connection["pageInfo"]
            next_cursor = info["endCursor"] if info["hasNextPage"] else None
            if info["hasNextPage"] and not next_cursor:
                raise SyncError("Linear pagination failed; retry the import")
        if next_cursor and next_cursor == cursor:
            raise SyncError("The provider repeated a page; retry the import")
        # Cursor APIs can repeat boundary records while issues change during a scan.
        normalized = {str(issue["id"]): self.normalize(provider, issue) for issue in issues}
        return list(normalized.values()), next_cursor

    def normalize(self, provider, issue):
        if provider == "jira":
            fields = issue["fields"]
            owner = (fields.get("assignee") or {}).get("displayName")
            priority = (fields.get("priority") or {}).get("name", "Medium").lower()
            priority = {"highest": "urgent", "high": "high", "lowest": "low", "low": "low"}.get(
                priority, "medium"
            )
            return RemoteTask(
                id=str(issue["id"]),
                key=issue["key"],
                title=fields["summary"][:512],
                description=adf_text(fields.get("description")).strip()[:100000] or None,
                owner=owner[:128] if owner else None,
                due_date=date_value(fields.get("duedate")),
                status=mapped_status(fields["status"], self.settings.jira_status_map, linear=False),
                priority=priority,
                updated_at=fields.get("updated"),
                url=safe_link(
                    self.settings.jira_base_url.rstrip("/")
                    + "/browse/"
                    + quote(issue["key"], safe="")
                ),
            )
        owner = (issue.get("assignee") or {}).get("name")
        return RemoteTask(
            id=issue["id"],
            key=issue.get("identifier"),
            title=issue["title"][:512],
            description=(issue.get("description") or "")[:100000] or None,
            owner=owner[:128] if owner else None,
            due_date=date_value(issue.get("dueDate")),
            status=mapped_status(issue["state"], self.settings.linear_status_map, linear=True),
            priority={1: "urgent", 2: "high", 3: "medium", 4: "low"}.get(
                issue.get("priority"), "medium"
            ),
            url=safe_link(issue.get("url")),
            updated_at=issue.get("updatedAt"),
        )


async def upsert_task(db, provider, remote, settings):
    scope = identity_scope(provider, settings)
    mirror = (
        (Task.linear_issue_id == remote.id)
        if provider == "linear"
        else and_(
            Task.jira_issue_key == remote.key,
            Task.jira_issue_url == remote.url,
        )
    )
    task = await db.scalar(
        select(Task)
        .where(
            Task.workspace_id == workspace_id(),
            or_(
                and_(
                    Task.source == provider,
                    Task.external_scope == scope,
                    Task.external_id == remote.id,
                ),
                mirror,
            ),
        )
        .with_for_update()
    )
    created = task is None
    if created:
        task = Task(
            id=uuid.uuid4(),
            workspace_id=workspace_id(),
            source=provider,
            external_scope=scope,
            external_id=remote.id,
            sync_pending=False,
            last_status_change_at=datetime.now(UTC),
        )
        db.add(task)
    before = {
        key: getattr(task, key)
        for key in (
            "title",
            "description",
            "owner",
            "due_date",
            "status",
            "priority",
        )
    }
    for key in before:
        # A local status change cannot be erased by a stale remote snapshot.
        if key != "status" or not task.sync_pending:
            setattr(task, key, getattr(remote, key))
    if provider == "jira":
        task.jira_issue_key, task.jira_issue_url = remote.key, remote.url
    else:
        task.linear_issue_id, task.linear_issue_url = remote.id, remote.url
    if before["status"] != task.status:
        task.last_status_change_at = datetime.now(UTC)
    changed = [key for key in before if before[key] != getattr(task, key)]
    if created or changed:
        db.add(
            TaskActivity(
                task_id=task.id,
                workspace_id=workspace_id(),
                actor=provider,
                kind="connector_imported" if created else "connector_updated",
                payload={"source": provider, "fields": changed},
            )
        )
    return "created" if created else "updated" if changed else "unchanged"


async def import_connected_tasks(db: AsyncSession, *, force=False):
    settings = get_settings()
    results = []
    for provider in PROVIDERS:
        if not enabled(provider, settings):
            continue
        # Same lock in the API and worker; transaction release also handles crashes.
        acquired = await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 2))"),
            {"key": f"task-import:{workspace_id()}:{provider}"},
        )
        if not acquired:
            await db.commit()
            results.append({"provider": provider, "busy": True, "created": 0, "updated": 0})
            continue
        state = await db.scalar(
            select(TaskImportState)
            .where(
                TaskImportState.workspace_id == workspace_id(),
                TaskImportState.provider == provider,
            )
            .with_for_update()
        )
        scope = destination_scope(provider, settings)
        if state is None:
            state = TaskImportState(workspace_id=workspace_id(), provider=provider, scope=scope)
            db.add(state)
            await db.flush()
        if state.scope != scope:
            state.scope, state.cursor, state.last_synced_at, state.last_attempt_at = (
                scope,
                None,
                None,
                None,
            )
        now = datetime.now(UTC)
        if (
            not force
            and state.last_attempt_at
            and state.last_attempt_at
            > now - timedelta(seconds=settings.integration_interval_seconds)
        ):
            await db.commit()
            continue
        state.last_attempt_at = now
        result = {"provider": provider, "created": 0, "updated": 0}
        try:
            async with db.begin_nested():
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    adapter = ImportClient(client)
                    for _ in range(PAGES_PER_PASS):
                        tasks, cursor = await adapter.page(provider, state.cursor)
                        for remote in tasks:
                            outcome = await upsert_task(db, provider, remote, settings)
                            if outcome in result:
                                result[outcome] += 1
                        await db.flush()
                        state.cursor = cursor
                        if cursor is None:
                            state.last_synced_at = now
                            break
                    state.error = None
        except (httpx.HTTPError, OAuthError, SyncError, KeyError, ValueError, TypeError) as exc:
            # Reload after savepoint rollback: SQLAlchemy expires changed attributes.
            await db.refresh(state)
            state.last_attempt_at = now
            state.error = "Task import failed. Check the connector's read access and try Sync now."
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400:
                # Provider cursors may expire; preserve records and restart the next scan.
                state.cursor = None
            result["created"], result["updated"] = 0, 0
        result.update(error=state.error, has_more=bool(state.cursor))
        await db.commit()
        results.append(result)
    return results


async def import_status(db):
    settings = get_settings()
    rows = (
        await db.scalars(
            select(TaskImportState).where(
                TaskImportState.workspace_id == workspace_id(),
            )
        )
    ).all()
    states = {row.provider: row for row in rows}
    result = []
    for provider in PROVIDERS:
        connected = enabled(provider, settings)
        state = states.get(provider)
        if not connected or (state and state.scope != destination_scope(provider, settings)):
            state = None
        result.append(
            {
                "provider": provider,
                "connected": connected,
                "last_synced_at": state.last_synced_at if state else None,
                "last_attempt_at": state.last_attempt_at if state else None,
                "has_more": bool(state and state.cursor),
                "error": state.error if state else None,
            }
        )
    return {
        "providers": result,
        "interval_seconds": settings.integration_interval_seconds,
        "status_sync_enabled": settings.task_sync_enabled,
    }
