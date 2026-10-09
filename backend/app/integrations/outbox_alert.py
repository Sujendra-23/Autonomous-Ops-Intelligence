"""Alerting for the webhook outbox: failed deliveries and a worker that stopped delivering.

The worker calls `check_outbox_alerts` on every integration tick. When the outbox is
unhealthy it writes an error log line (`outbox.unhealthy`) and, if Slack is configured, posts
one message to the default channel. Alerts repeat at most once per cooldown and a single
"recovered" message follows when the outbox is healthy again. The cooldown lives in worker
memory, so a worker restart may repeat an alert that is still open.

Alert conditions (see `docs/runbook.md`):

- `failed` deliveries at or above `OUTBOX_ALERT_FAILED_THRESHOLD`. Failed rows never retry
  on their own, so they need an operator.
- `overdue` pending deliveries whose `next_attempt_at` passed more than
  `OUTBOX_ALERT_STALE_SECONDS` ago. The worker is down, stuck, or far behind.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.integrations.slack import SlackAdapter
from app.logging import get_logger
from app.models.integration import WebhookDelivery
from app.tenancy import workspace_context

logger = get_logger("app.integrations.outbox_alert")

# workspace key -> time.monotonic() of the last alert still considered open.
_open_alerts: dict[str, float] = {}


@dataclass(frozen=True)
class OutboxHealth:
    pending: int
    failed: int
    overdue: int
    oldest_overdue_seconds: int | None

    def as_dict(self) -> dict:
        return asdict(self)

    def problems(self, settings: Settings) -> list[str]:
        found = []
        if self.failed >= settings.outbox_alert_failed_threshold:
            found.append(f"{self.failed} failed webhook deliveries need a manual retry")
        if self.overdue:
            found.append(
                f"{self.overdue} pending deliveries are overdue by up to "
                f"{self.oldest_overdue_seconds // 60} minutes, so the worker may be down or behind"
            )
        return found


async def outbox_health(session: AsyncSession, now: datetime | None = None) -> OutboxHealth:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=get_settings().outbox_alert_stale_seconds)
    pending = WebhookDelivery.status == "pending"
    overdue = and_(pending, WebhookDelivery.next_attempt_at <= cutoff)
    row = (
        await session.execute(
            select(
                func.count().filter(pending),
                func.count().filter(WebhookDelivery.status == "failed"),
                func.count().filter(overdue),
                func.min(WebhookDelivery.next_attempt_at).filter(overdue),
            )
        )
    ).one()
    oldest = row[3]
    return OutboxHealth(
        pending=row[0],
        failed=row[1],
        overdue=row[2],
        oldest_overdue_seconds=int((now - oldest).total_seconds()) if oldest else None,
    )


async def _notify(text: str) -> None:
    slack = SlackAdapter()
    if not slack.is_enabled():
        return
    try:
        await slack.send_reminder(text)
    except Exception:  # An alert failure must not stop the scheduler.
        logger.error("outbox.alert_delivery_failed")


async def check_outbox_alerts(session: AsyncSession) -> bool:
    """Return True when an alert was raised on this call."""
    settings = get_settings()
    if not settings.webhook_url.get_secret_value():
        return False
    health = await outbox_health(session)
    key = str(workspace_context.get())
    problems = health.problems(settings)
    now = time.monotonic()
    if not problems:
        if _open_alerts.pop(key, None) is not None:
            logger.info("outbox.recovered", **health.as_dict())
            await _notify(":white_check_mark: Webhook outbox recovered, nothing failed or overdue.")
        return False
    last = _open_alerts.get(key)
    if last is not None and now - last < settings.outbox_alert_cooldown_seconds:
        return False
    _open_alerts[key] = now
    logger.error("outbox.unhealthy", problems=problems, **health.as_dict())
    await _notify(
        ":rotating_light: Webhook outbox needs attention: "
        + "; ".join(problems)
        + ". Runbook: docs/runbook.md#webhook-outbox"
    )
    return True
