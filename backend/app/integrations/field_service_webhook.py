"""Inbound field-service webhooks: HMAC verification and event application.

The signature scheme mirrors this project's own outbound webhooks: ``X-FS-Signature`` is
``sha256=`` + HMAC-SHA256 of ``"<timestamp>.<raw body>"`` using the shared secret, and
``X-FS-Timestamp`` must be within a replay window. This is the scheme the bundled mock server
signs with; a real provider's signing scheme (if it has one) must be mapped here before use.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.webhooks import enqueue_event, task_data
from app.logging import get_logger
from app.models.task import Task, TaskActivity

logger = get_logger("app.integrations.field_service_webhook")

REPLAY_WINDOW_SECONDS = 300
ACTIVITY_KIND = "field_service_event"
# Provider event type -> AOI task status. Appointment events are recorded but do not move status.
STATUS_BY_EVENT = {
    "job.completed": "done",
    "job.canceled": "cancelled",
    "job.cancelled": "cancelled",
    "job.hold": "blocked",
}
TERMINAL = {"done", "cancelled"}


def verify_signature(
    secret: str,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    now: float | None = None,
) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - sent_at) > REPLAY_WINDOW_SECONDS:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest("sha256=" + expected.hexdigest(), signature)


async def apply_event(session: AsyncSession, event: dict) -> str:
    """Apply one verified event. Returns 'applied', 'duplicate', 'ignored' or 'unknown_job'.

    Safe to call repeatedly with the same event id (providers retry): the first application
    records a TaskActivity carrying the event id, and later deliveries are acknowledged
    without side effects.
    """
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    job_id = str((event.get("data") or {}).get("jobId") or "")
    if not event_id or not event_type or not job_id:
        return "ignored"

    task = await session.scalar(select(Task).where(Task.field_service_job_id == job_id))
    if task is None:
        return "unknown_job"
    seen = await session.scalar(
        select(TaskActivity.id).where(
            TaskActivity.task_id == task.id,
            TaskActivity.kind == ACTIVITY_KIND,
            TaskActivity.payload["event_id"].astext == event_id,
        )
    )
    if seen is not None:
        return "duplicate"

    target = STATUS_BY_EVENT.get(event_type)
    changed = target is not None and target != task.status and task.status not in TERMINAL
    session.add(
        TaskActivity(
            task_id=task.id,
            kind=ACTIVITY_KIND,
            actor="field_service",
            payload={
                "event_id": event_id,
                "type": event_type,
                "from": task.status if changed else None,
                "to": target if changed else None,
            },
        )
    )
    if changed:
        task.status = target
        task.last_status_change_at = datetime.now(UTC)
        enqueue_event(session, "task.updated", {**task_data(task), "source": "field_service"})
    await session.commit()
    logger.info("field_service.event_applied", type=event_type, changed=changed)
    return "applied"
