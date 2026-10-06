"""Long-running scheduler that ticks the drift monitor on an interval.

This is the entry point for the `worker` container. It connects to the same
database the API uses and runs `DriftMonitor.scan(notify=True)` every
`MONITOR_INTERVAL_SECONDS`. We intentionally keep it dependency-light — no
Celery, no Temporal — because the workload is naturally periodic and the
project already needs Postgres + Redis. A more ambitious rewrite that uses
Temporal lives in the README as a Phase 3 follow-up.
"""

from __future__ import annotations

import asyncio
import signal

from app.config import get_settings
from app.database import SessionLocal
from app.integrations.task_sync import sync_task_statuses
from app.integrations.webhooks import deliver_webhooks
from app.logging import configure_logging, get_logger
from app.workers.monitor import DriftMonitor


async def _run_once() -> None:
    log = get_logger("app.workers.scheduler")
    async with SessionLocal() as session:
        monitor = DriftMonitor(session)
        try:
            findings = await monitor.scan(notify=True)
            log.info("scheduler.tick", findings=len(findings))
        except Exception:
            log.exception("scheduler.tick_failed")


async def _run_integrations() -> None:
    log = get_logger("app.workers.scheduler")
    for operation in (sync_task_statuses, deliver_webhooks):
        async with SessionLocal() as session:
            try:
                await operation(session)
            except Exception:  # One unavailable provider must not stop the scheduler.
                await session.rollback()
                log.error("scheduler.integration_failed", operation=operation.__name__)


async def main() -> None:
    configure_logging()
    log = get_logger("app.workers.scheduler")
    settings = get_settings()
    interval = min(settings.monitor_interval_seconds, settings.integration_interval_seconds)

    stop = asyncio.Event()

    def _stop(_signum, _frame):
        log.info("scheduler.shutdown_requested")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop, sig, None)

    log.info("scheduler.start", interval_seconds=interval)
    next_monitor = 0.0
    while not stop.is_set():
        await _run_integrations()
        if loop.time() >= next_monitor:
            await _run_once()
            next_monitor = loop.time() + settings.monitor_interval_seconds
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    log.info("scheduler.stopped")


if __name__ == "__main__":
    asyncio.run(main())
