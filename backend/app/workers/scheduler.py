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

from sqlalchemy import and_, or_, select, text

from app.config import base_settings, get_settings
from app.database import SessionLocal, engine
from app.integrations.task_sync import sync_task_statuses
from app.integrations.webhooks import deliver_webhooks
from app.logging import configure_logging, get_logger
from app.models.account import Workspace
from app.models.transcript import Transcript
from app.services.extraction import ExtractionPipeline
from app.tenancy import LEGACY_WORKSPACE, settings_context, workspace_context, workspace_settings
from app.workers.monitor import DriftMonitor


async def in_workspaces(operation):
    if base_settings().auth_mode != "oidc":
        return await operation()
    async with SessionLocal() as db:
        workspaces = (
            await db.scalars(select(Workspace).where(Workspace.id != LEGACY_WORKSPACE))
        ).all()
    for workspace in workspaces:
        try:
            scoped_settings = workspace_settings(workspace.connector_ciphertext)
        except Exception:
            get_logger("app.workers.scheduler").error(
                "scheduler.workspace_config_failed", workspace_id=str(workspace.id)
            )
            continue
        token = workspace_context.set(workspace.id)
        config_token = settings_context.set(scoped_settings)
        try:
            async with engine.connect() as lease:
                key = str(workspace.id)
                acquired = await lease.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key,1))"), {"key": key}
                )
                if acquired:
                    try:
                        await operation()
                    finally:
                        await lease.execute(
                            text("SELECT pg_advisory_unlock(hashtextextended(:key,1))"),
                            {"key": key},
                        )
        except Exception:
            get_logger("app.workers.scheduler").error(
                "scheduler.workspace_operation_failed", workspace_id=str(workspace.id)
            )
        finally:
            settings_context.reset(config_token)
            workspace_context.reset(token)


async def _run_once() -> None:
    log = get_logger("app.workers.scheduler")
    async with SessionLocal() as session:
        monitor = DriftMonitor(session)
        try:
            findings = await monitor.scan(notify=True)
            log.info("scheduler.tick", findings=len(findings))
        except Exception:
            log.exception("scheduler.tick_failed")


async def _run_extraction() -> None:
    async with SessionLocal() as db:
        ids = (
            await db.scalars(
                select(Transcript.id)
                .where(
                    or_(
                        Transcript.status == "received",
                        and_(
                            Transcript.source != "live",
                            Transcript.status.in_(("chunking", "extracting")),
                        ),
                    )
                )
                .order_by(Transcript.created_at)
                .limit(5)
            )
        ).all()
        for transcript_id in ids:
            try:
                await ExtractionPipeline(db).process(transcript_id)
            except Exception:
                await db.rollback()
                get_logger("app.workers.scheduler").error("scheduler.extraction_failed")


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
        await in_workspaces(_run_extraction)
        await in_workspaces(_run_integrations)
        if loop.time() >= next_monitor:
            await in_workspaces(_run_once)
            next_monitor = loop.time() + settings.monitor_interval_seconds
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    log.info("scheduler.stopped")


if __name__ == "__main__":
    asyncio.run(main())
