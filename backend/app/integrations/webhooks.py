"""Transactional outbox with signed, at-least-once webhook delivery."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.integration import WebhookDelivery
from app.models.task import Task


def task_data(task: Task) -> dict:
    return {
        "id": str(task.id),
        "project_id": str(task.project_id) if task.project_id else None,
        "title": task.title,
        "status": task.status,
        "owner": task.owner,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


def enqueue_event(session: AsyncSession, event_type: str, data: dict) -> None:
    settings = get_settings()
    destination = settings.webhook_url.get_secret_value()
    if not destination or event_type not in settings.webhook_events:
        return
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add(
        WebhookDelivery(
            id=event_id,
            event_type=event_type,
            destination=destination,
            payload={
                "id": str(event_id),
                "type": event_type,
                "version": 1,
                "occurred_at": now.isoformat(),
                "data": data,
            },
            status="pending",
            attempts=0,
            next_attempt_at=now,
        )
    )


def signed_headers(body: bytes, event_id: str, secret: str, timestamp: int) -> dict:
    signature = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-AOI-Event-ID": event_id,
        "X-AOI-Timestamp": str(timestamp),
        "X-AOI-Signature": f"sha256={signature}",
    }


async def deliver_webhooks(session: AsyncSession, limit: int = 50) -> int:
    settings = get_settings()
    if not settings.webhook_url.get_secret_value():
        return 0
    delivered = 0
    # Hold a row lock through each bounded HTTP request. Multiple workers cannot
    # send the same row concurrently; a crash after receipt may still duplicate it.
    for _ in range(limit):
        row = await session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.next_attempt_at <= datetime.now(UTC),
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            await session.commit()
            break
        if row.destination != settings.webhook_url.get_secret_value():
            row.status = "failed"
            row.last_error = "Destination changed; restore configuration before retrying"
            await session.commit()
            continue
        body = json.dumps(row.payload, sort_keys=True, separators=(",", ":")).encode()
        row.attempts += 1
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    row.destination,
                    content=body,
                    headers=signed_headers(
                        body,
                        str(row.id),
                        settings.webhook_secret.get_secret_value(),
                        int(datetime.now(UTC).timestamp()),
                    ),
                )
                response.raise_for_status()
            row.status = "delivered"
            row.last_error = None
            delivered += 1
        except httpx.HTTPError as exc:
            # Never persist URL, response body, or signing secret in errors.
            row.last_error = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else "Network error"
            )
            permanent = (
                isinstance(exc, httpx.HTTPStatusError)
                and 400 <= exc.response.status_code < 500
                and exc.response.status_code not in (408, 429)
            )
            row.status = "failed" if permanent or row.attempts >= 8 else "pending"
            row.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=min(3600, 30 * 2**row.attempts)
            )
        await session.commit()
    return delivered
