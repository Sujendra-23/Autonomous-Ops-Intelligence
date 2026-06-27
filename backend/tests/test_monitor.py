"""Drift monitor tests against a real database."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.blocker import Blocker
from app.models.task import Task
from app.workers.monitor import DriftMonitor


@pytest.mark.asyncio
async def test_monitor_flags_overdue_tasks(db_session) -> None:
    yesterday = datetime.utcnow() - timedelta(days=2)
    db_session.add(
        Task(
            title="Ship the migration",
            owner="John",
            priority="high",
            status="in_progress",
            due_date=yesterday,
        )
    )
    await db_session.commit()

    findings = await DriftMonitor(db_session).scan(notify=False)
    kinds = {f["kind"] for f in findings}
    assert "overdue" in kinds
    assert any(f["severity"] == "critical" for f in findings if f["kind"] == "overdue")


@pytest.mark.asyncio
async def test_monitor_flags_stalled_tasks(db_session) -> None:
    long_ago = datetime.utcnow() - timedelta(days=30)
    db_session.add(
        Task(
            title="Stalled work",
            owner="Priya",
            status="in_progress",
            last_status_change_at=long_ago,
        )
    )
    await db_session.commit()

    findings = await DriftMonitor(db_session).scan(notify=False)
    assert any(f["kind"] == "stalled" for f in findings)


@pytest.mark.asyncio
async def test_monitor_flags_missing_owner(db_session) -> None:
    db_session.add(
        Task(title="High priority but no owner", status="open", priority="urgent")
    )
    await db_session.commit()

    findings = await DriftMonitor(db_session).scan(notify=False)
    assert any(f["kind"] == "missing_owner" for f in findings)


@pytest.mark.asyncio
async def test_monitor_flags_aged_blockers(db_session) -> None:
    old = datetime.utcnow() - timedelta(days=5)
    blocker = Blocker(summary="Need prod access", status="open")
    db_session.add(blocker)
    await db_session.commit()
    # Backdate created_at — the default server_default is `now()`.
    blocker.created_at = old
    await db_session.commit()

    findings = await DriftMonitor(db_session).scan(notify=False)
    assert any(f["kind"] == "unresolved_blocker" for f in findings)


@pytest.mark.asyncio
async def test_monitor_does_not_flag_healthy_tasks(db_session) -> None:
    next_week = datetime.utcnow() + timedelta(days=7)
    db_session.add(
        Task(
            title="On track",
            owner="Sam",
            priority="medium",
            status="in_progress",
            due_date=next_week,
        )
    )
    await db_session.commit()

    findings = await DriftMonitor(db_session).scan(notify=False)
    assert findings == []
