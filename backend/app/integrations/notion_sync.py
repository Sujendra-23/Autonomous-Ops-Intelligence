"""Reconcile mapped Notion checkboxes under a row lock; pending AOI edits win."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.notion import _NOTION_API, NotionAdapter
from app.integrations.notion_permissions import NotionAccessError, require_notion_write
from app.integrations.webhooks import enqueue_event, task_data
from app.models.project import Project
from app.models.task import Task, TaskActivity


async def sync_notion_statuses(
    session: AsyncSession, limit: int = 100, *, page_id: str | None = None
) -> int:
    settings = get_settings()
    if not settings.notion_sync_enabled or not settings.notion_enabled:
        return 0
    require_notion_write()
    updated = 0
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.integration_interval_seconds)
    # Process a bounded batch, oldest first; subsequent polls catch overflow.
    query = select(Task).where(Task.notion_block_id.is_not(None))
    if page_id:
        query = query.join(Project, Task.project_id == Project.id).where(
            Project.notion_page_id == page_id
        )
    else:
        query = query.where(
            or_(Task.notion_sync_checked_at.is_(None), Task.notion_sync_checked_at <= cutoff)
        )
    rows = (
        await session.scalars(
            query.order_by(Task.notion_sync_checked_at.asc().nullsfirst(), Task.id)
            .limit(limit)
            .with_for_update(of=Task, skip_locked=True)
        )
    ).all()
    for task in rows:
        try:
            project = await session.get(Project, task.project_id) if task.project_id else None
            if project is None:
                raise NotionAccessError("Notion task has no project")
            adapter = NotionAdapter()
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                block = await adapter.check_block_scope(client, task, project)
                checked = block["to_do"]["checked"]
                if task.notion_sync_pending:
                    if checked != (task.status == "done"):
                        response = await client.patch(
                            f"{_NOTION_API}/blocks/{task.notion_block_id}",
                            headers=adapter._headers(),
                            json={"to_do": {"checked": task.status == "done"}},
                        )
                        response.raise_for_status()
                    session.add(
                        TaskActivity(
                            task_id=task.id,
                            kind="external_status_pushed",
                            actor="notion",
                            payload={"status": task.status},
                        )
                    )
                else:
                    # Unchecked preserves richer AOI states; only a completed task reopens.
                    status = "done" if checked else "open" if task.status == "done" else task.status
                    if status != task.status:
                        previous = task.status
                        task.status = status
                        task.last_status_change_at = datetime.now(UTC)
                        if settings.task_sync_enabled and (
                            task.linear_issue_id or task.jira_issue_key
                        ):
                            task.sync_pending = True
                            task.sync_checked_at = None
                        session.add(
                            TaskActivity(
                                task_id=task.id,
                                kind="external_status_change",
                                actor="notion",
                                payload={"from": previous, "to": status},
                            )
                        )
                        enqueue_event(
                            session, "task.updated", {**task_data(task), "source": "notion"}
                        )
                        updated += 1
            task.notion_sync_pending = False
            task.notion_sync_error = None
        except (httpx.HTTPError, NotionAccessError, KeyError, ValueError, TypeError):
            task.notion_sync_error = (
                "Notion sync failed; check connector permissions and project scope"
            )
        task.notion_sync_checked_at = datetime.now(UTC)
    # Commit the batch once: do not release other row locks halfway through reconciliation.
    await session.commit()
    return updated
