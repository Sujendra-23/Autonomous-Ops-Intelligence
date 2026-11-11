"""Leadership briefing filters, totals, ordering, evidence and workspace isolation."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.intelligence import router
from app.config import get_settings
from app.database import Base, get_session
from app.models.decision import Decision
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.weekly_summary import build_weekly_summary
from app.tenancy import workspace_context

NOW = datetime(2026, 10, 12, 18, tzinfo=UTC)


@pytest.fixture
async def summary_session(db_available):
    """Real PostgreSQL, isolated schema, no pgvector needed for summary records."""
    if not db_available:
        if os.environ.get("REQUIRE_DATABASE_TESTS") == "true":
            pytest.fail("Postgres required for weekly summary checks")
        pytest.skip("Postgres not reachable")
    engine = create_async_engine(os.environ.get("TEST_DATABASE_URL", get_settings().database_url))
    schema = "weekly_test_" + uuid.uuid4().hex
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
        await connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[model.__table__ for model in (Project, Transcript, Task, Risk, Decision)],
            )
        )
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


async def test_empty_summary_is_valid_and_read_only(summary_session):
    report = await build_weekly_summary(summary_session, now=NOW)
    assert report.open_risk_count == report.overdue_task_count == report.decision_count == 0
    assert report.open_risks == report.overdue_tasks == report.decisions == []
    assert report.period_start == NOW - timedelta(days=7)
    assert report.period_end == report.generated_at == NOW
    assert not summary_session.new and not summary_session.dirty


async def test_snapshot_includes_old_unresolved_work_and_excludes_inactive_tasks(summary_session):
    old = NOW - timedelta(days=30)
    summary_session.add_all(
        [
            Risk(title="Old open risk", status="open", created_at=old),
            Risk(title="Resolved risk", status="resolved"),
            *[
                Task(title=status, status=status, due_date=old)
                for status in ("open", "in_progress", "blocked", "done", "cancelled")
            ],
            Task(title="No deadline", status="open"),
            Task(title="Due exactly now", status="open", due_date=NOW),
            Task(title="Future deadline", status="open", due_date=NOW + timedelta(days=1)),
        ]
    )
    await summary_session.flush()
    report = await build_weekly_summary(summary_session, now=NOW)
    assert report.open_risk_count == 1
    assert report.open_risks[0].title == "Old open risk"
    assert report.overdue_task_count == 3
    assert {task.status for task in report.overdue_tasks} == {"open", "in_progress", "blocked"}


async def test_decisions_use_seven_day_capture_window_and_exclude_superseded(summary_session):
    start = NOW - timedelta(days=7)
    summary_session.add_all(
        [
            Decision(summary="At start", created_at=start),
            Decision(summary="Recent", created_at=NOW - timedelta(seconds=1), extra_metadata={}),
            Decision(
                summary="Still current",
                created_at=NOW - timedelta(days=1),
                extra_metadata={"superseded": False},
            ),
            Decision(summary="Too old", created_at=start - timedelta(seconds=1)),
            Decision(summary="At end", created_at=NOW),
            Decision(summary="Future", created_at=NOW + timedelta(seconds=1)),
            Decision(summary="Reversed", created_at=start, extra_metadata={"superseded": True}),
        ]
    )
    await summary_session.flush()
    report = await build_weekly_summary(summary_session, now=NOW)
    assert report.decision_count == 3
    assert [decision.summary for decision in report.decisions] == [
        "Recent",
        "Still current",
        "At start",
    ]


async def test_highlights_are_prioritized_but_totals_are_not_truncated(summary_session):
    summary_session.add_all(
        [
            *[
                Risk(title=severity, severity=severity)
                for severity in ("low", "medium", "high", "critical")
            ],
            *[
                Task(
                    title=priority,
                    priority=priority,
                    status="open",
                    due_date=NOW - timedelta(days=1),
                )
                for priority in ("low", "medium", "high", "urgent")
            ],
            *[
                Decision(summary=str(day), created_at=NOW - timedelta(days=day))
                for day in (1, 2, 3, 4)
            ],
        ]
    )
    await summary_session.flush()
    report = await build_weekly_summary(summary_session, now=NOW)
    assert report.open_risk_count == report.overdue_task_count == report.decision_count == 4
    assert [risk.severity for risk in report.open_risks] == ["critical", "high", "medium"]
    assert [task.priority for task in report.overdue_tasks] == ["urgent", "high", "medium"]
    assert [decision.summary for decision in report.decisions] == ["1", "2", "3"]


async def test_same_priority_tasks_put_oldest_deadline_first(summary_session):
    summary_session.add_all(
        [
            Task(title=str(day), priority="high", status="open", due_date=NOW - timedelta(days=day))
            for day in (1, 3, 2)
        ]
    )
    await summary_session.flush()
    report = await build_weekly_summary(summary_session, now=NOW)
    assert [task.title for task in report.overdue_tasks] == ["3", "2", "1"]


async def test_project_names_source_evidence_and_unassigned_records_are_preserved(summary_session):
    project = Project(name="Launch", slug="launch")
    summary_session.add(project)
    await summary_session.flush()
    transcript = Transcript(
        title="Launch review", content="We need approval.", project_id=project.id
    )
    summary_session.add(transcript)
    await summary_session.flush()
    risk = Risk(
        title="Approval risk",
        project_id=project.id,
        transcript_id=transcript.id,
        source_quote="We need approval.",
        confidence=0.91,
        mitigation="Escalate approval",
    )
    summary_session.add_all(
        [
            risk,
            Task(title="Unassigned task", due_date=NOW - timedelta(days=1), status="open"),
            Decision(
                summary="Proceed",
                decided_by=["Sam"],
                rationale="Scope approved",
                created_at=NOW - timedelta(days=1),
            ),
        ]
    )
    await summary_session.flush()
    report = await build_weekly_summary(summary_session, now=NOW)
    assert report.open_risks[0].project_name == "Launch"
    assert report.open_risks[0].transcript_id == transcript.id
    assert report.open_risks[0].source_quote == "We need approval."
    assert report.open_risks[0].confidence == 0.91
    assert report.overdue_tasks[0].project_name is None
    assert report.overdue_tasks[0].owner is None
    assert report.decisions[0].decided_by == ["Sam"]


async def test_counts_highlights_and_project_names_stay_in_workspace(summary_session, monkeypatch):
    monkeypatch.setattr("app.config.base_settings", lambda: SimpleNamespace(auth_mode="oidc"))
    workspaces = [uuid.uuid4(), uuid.uuid4()]
    for workspace, label in zip(workspaces, ("Visible", "Private"), strict=True):
        token = workspace_context.set(workspace)
        try:
            project = Project(name=label, slug=label.lower())
            summary_session.add(project)
            await summary_session.flush()
            summary_session.add_all(
                [
                    Risk(title=label, project_id=project.id),
                    Task(title=label, project_id=project.id, due_date=NOW - timedelta(days=1)),
                    Decision(
                        summary=label, project_id=project.id, created_at=NOW - timedelta(days=1)
                    ),
                ]
            )
            await summary_session.flush()
        finally:
            workspace_context.reset(token)
    for workspace, label in zip(workspaces, ("Visible", "Private"), strict=True):
        token = workspace_context.set(workspace)
        try:
            report = await build_weekly_summary(summary_session, now=NOW)
            assert report.open_risk_count == report.overdue_task_count == report.decision_count == 1
            assert report.open_risks[0].title == report.open_risks[0].project_name == label
            assert report.overdue_tasks[0].title == report.decisions[0].summary == label
        finally:
            workspace_context.reset(token)


async def test_http_endpoint_returns_serialized_briefing(summary_session):
    app = FastAPI()
    app.include_router(router, prefix="/api/intelligence")

    async def session_override():
        yield summary_session

    app.dependency_overrides[get_session] = session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/intelligence/weekly-summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["open_risk_count"] == 0
    assert payload["generated_at"].endswith("Z")
    assert payload["decisions"] == []
