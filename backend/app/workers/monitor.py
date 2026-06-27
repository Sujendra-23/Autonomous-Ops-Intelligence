"""Drift detection — the operational-intelligence brain.

Each `scan()` evaluates four categories of drift over the current database
state and (optionally) fires reminders. Findings are persisted to the
`task_activities` log for auditability.

Detection rules
---------------

1. **Overdue tasks** — `due_date` has passed, status is still active.
2. **Stalled tasks** — active status, but `last_status_change_at` older than
   `STALL_DAYS`.
3. **Missing owner** — active, high-or-urgent priority, no owner set.
4. **Unresolved blockers** — `Blocker.status == "open"` for more than 3 days.

When `notify=True` and Slack is configured, one message per finding is sent
at most every 24 hours (tracked via `last_reminder_at` on the task).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.slack import SlackAdapter
from app.logging import get_logger
from app.models.blocker import Blocker
from app.models.task import Task, TaskActivity

logger = get_logger("app.workers.monitor")

REMINDER_COOLDOWN_HOURS = 24
BLOCKER_AGE_THRESHOLD_DAYS = 3


class DriftMonitor:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._slack = SlackAdapter()

    async def scan(self, *, notify: bool = False) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        settings = get_settings()
        findings: list[dict[str, Any]] = []

        findings.extend(await self._overdue_tasks(now, settings.overdue_grace_days))
        findings.extend(await self._stalled_tasks(now, settings.stall_days))
        findings.extend(await self._missing_owners())
        findings.extend(await self._unresolved_blockers(now))

        await self._record_findings(findings)
        if notify:
            await self._notify(findings, now)
        await self._session.commit()
        logger.info("monitor.scan", findings=len(findings), notified=notify)
        return findings

    # ---- detectors -------------------------------------------------------- #

    async def _overdue_tasks(self, now: datetime, grace_days: int) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=grace_days)
        rows = (
            await self._session.execute(
                select(Task).where(
                    Task.status.in_(("open", "in_progress", "blocked")),
                    Task.due_date.is_not(None),
                    Task.due_date < cutoff,
                )
            )
        ).scalars().all()
        return [
            {
                "kind": "overdue",
                "severity": "critical" if t.priority in ("high", "urgent") else "warning",
                "task_id": t.id,
                "blocker_id": None,
                "project_id": t.project_id,
                "title": t.title,
                "detail": (
                    f"Due {t.due_date.date().isoformat() if t.due_date else 'unknown'}; "
                    f"owner: {t.owner or 'unassigned'}"
                ),
            }
            for t in rows
        ]

    async def _stalled_tasks(self, now: datetime, stall_days: int) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=stall_days)
        rows = (
            await self._session.execute(
                select(Task).where(
                    Task.status.in_(("open", "in_progress")),
                    Task.last_status_change_at < cutoff,
                )
            )
        ).scalars().all()
        return [
            {
                "kind": "stalled",
                "severity": "warning",
                "task_id": t.id,
                "blocker_id": None,
                "project_id": t.project_id,
                "title": t.title,
                "detail": (
                    f"No status change in {stall_days}+ days; "
                    f"owner: {t.owner or 'unassigned'}"
                ),
            }
            for t in rows
        ]

    async def _missing_owners(self) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                select(Task).where(
                    Task.status.in_(("open", "in_progress", "blocked")),
                    Task.priority.in_(("high", "urgent")),
                    (Task.owner.is_(None)) | (Task.owner == ""),
                )
            )
        ).scalars().all()
        return [
            {
                "kind": "missing_owner",
                "severity": "warning",
                "task_id": t.id,
                "blocker_id": None,
                "project_id": t.project_id,
                "title": t.title,
                "detail": f"Priority {t.priority} but no owner assigned",
            }
            for t in rows
        ]

    async def _unresolved_blockers(self, now: datetime) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=BLOCKER_AGE_THRESHOLD_DAYS)
        rows = (
            await self._session.execute(
                select(Blocker).where(
                    Blocker.status == "open",
                    Blocker.created_at < cutoff,
                )
            )
        ).scalars().all()
        return [
            {
                "kind": "unresolved_blocker",
                "severity": "critical" if b.severity in ("high", "critical") else "warning",
                "task_id": b.task_id,
                "blocker_id": b.id,
                "project_id": b.project_id,
                "title": b.summary,
                "detail": (
                    f"Open for {(now - b.created_at).days}+ days; "
                    f"blocked: {b.blocked_party or 'unknown'}; "
                    f"needs: {b.needs_from or 'unknown'}"
                ),
            }
            for b in rows
        ]

    # ---- side effects ----------------------------------------------------- #

    async def _record_findings(self, findings: list[dict[str, Any]]) -> None:
        for item in findings:
            if item.get("task_id") is None:
                continue
            self._session.add(
                TaskActivity(
                    task_id=item["task_id"],
                    kind=f"drift_{item['kind']}",
                    payload={"severity": item["severity"], "detail": item["detail"]},
                    actor="monitor",
                )
            )

    async def _notify(self, findings: list[dict[str, Any]], now: datetime) -> None:
        if not findings or not self._slack.is_enabled():
            return
        cooldown = timedelta(hours=REMINDER_COOLDOWN_HOURS)

        for item in findings:
            task_id = item.get("task_id")
            if task_id is None:
                # Blocker without a linked task — fire a generic message.
                await self._slack.send_reminder(
                    f":rotating_light: *{item['title']}* — {item['detail']}"
                )
                continue
            task = await self._session.get(Task, task_id)
            if task is None:
                continue
            if task.last_reminder_at and (now - task.last_reminder_at) < cooldown:
                continue
            emoji = {
                "critical": ":rotating_light:",
                "warning": ":warning:",
                "info": ":information_source:",
            }.get(item["severity"], ":warning:")
            text = (
                f"{emoji} *{item['title']}*\n"
                f"_{item['kind'].replace('_', ' ').title()}_ — {item['detail']}"
            )
            if task.linear_issue_url:
                text += f"\n<{task.linear_issue_url}|Linear>"
            elif task.jira_issue_url:
                text += f"\n<{task.jira_issue_url}|Jira>"
            await self._slack.send_reminder(text)
            task.last_reminder_at = now
            self._session.add(
                TaskActivity(
                    task_id=task.id,
                    kind="reminder_sent",
                    payload={"channel": "slack", "kind": item["kind"]},
                    actor="monitor",
                )
            )
