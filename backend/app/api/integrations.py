"""Integration configuration status, calendar selection, and delivery diagnostics."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ingest_key
from app.config import get_settings
from app.database import get_session
from app.integrations.calendar import GoogleCalendar
from app.models.integration import WebhookDelivery
from app.models.project import Project
from app.models.task import Task

router = APIRouter(dependencies=[Depends(require_ingest_key)])


@router.get("/status")
async def integration_status() -> dict:
    s = get_settings()
    return {
        "google_calendar": s.google_calendar_enabled,
        "notion": s.notion_enabled,
        "notion_sync": s.notion_sync_enabled,
        "slack": s.slack_enabled,
        "discord": s.discord_enabled,
        "teams": s.teams_enabled,
        "task_sync": s.task_sync_enabled,
        "linear": s.linear_enabled,
        "jira": s.jira_enabled,
        "salesforce": s.salesforce_enabled,
        "webhooks": bool(s.webhook_url.get_secret_value()),
        "webhook_events": s.webhook_events,
        "interval_seconds": s.integration_interval_seconds,
    }


@router.get("/calendar/events")
async def calendar_events(
    start: datetime | None = None,
    end: datetime | None = None,
    page_token: str | None = Query(None, max_length=4096),
) -> dict:
    start = start or datetime.now(UTC) - timedelta(hours=2)
    end = end or start + timedelta(days=7)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, "Calendar dates must include a timezone offset")
    if not timedelta(0) < end - start <= timedelta(days=31):
        raise HTTPException(422, "Calendar range must be positive and no longer than 31 days")
    return await GoogleCalendar().list_events(start, end, page_token)


@router.get("/webhooks/deliveries")
async def webhook_deliveries(
    limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = (
        await session.scalars(
            select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit)
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "event_type": r.event_type,
            "status": r.status,
            "attempts": r.attempts,
            "last_error": r.last_error,
            "next_attempt_at": r.next_attempt_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.post("/webhooks/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(WebhookDelivery, delivery_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "Delivery not found")
    if row.status != "failed":
        raise HTTPException(409, "Only failed deliveries can be retried")
    if row.destination != get_settings().webhook_url.get_secret_value():
        raise HTTPException(409, "Restore the original webhook destination before retrying")
    row.status, row.attempts, row.last_error = "pending", 0, None
    row.next_attempt_at = datetime.now(UTC)
    await session.commit()
    return {"id": str(row.id), "status": row.status}


class NotionReconcileRequest(BaseModel):
    page_id: uuid.UUID


@router.post("/notion/reconcile")
async def reconcile_notion(
    payload: NotionReconcileRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    """Schedule a bounded workspace batch; provider calls stay in the worker."""
    from app.integrations.notion_permissions import NotionAccessError, require_notion_write

    try:
        require_notion_write()
        if not get_settings().notion_sync_enabled or not get_settings().notion_enabled:
            raise HTTPException(409, "Enable the workspace Notion connector and status sync")
        rows = (
            await session.scalars(
                select(Task)
                .join(Project, Task.project_id == Project.id)
                .where(
                    Project.notion_page_id == str(payload.page_id),
                    Task.notion_block_id.is_not(None),
                )
                .limit(500)
                .with_for_update(of=Task, skip_locked=True)
            )
        ).all()
        for task in rows:
            task.notion_sync_checked_at = None
        await session.commit()
    except NotionAccessError:
        raise HTTPException(403, "Writable workspace membership required") from None
    return {"scheduled": len(rows)}
