"""Generate a leadership briefing without an LLM call or external side effects.

Risks and overdue tasks reflect current state, including older unresolved work.
Decisions are records captured in the trailing seven days and still in effect.
Counts cover all matching records; each section highlights at most three.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import Decision
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from app.schemas.weekly_summary import (
    SummaryDecision,
    SummaryRisk,
    SummaryTask,
    WeeklySummary,
)

HIGHLIGHT_LIMIT = 3


async def build_weekly_summary(
    session: AsyncSession, *, now: datetime | None = None
) -> WeeklySummary:
    now = now or datetime.now(UTC)
    period_start = now - timedelta(days=7)
    risk_filters = (Risk.status == "open",)
    task_filters = (
        Task.status.in_(("open", "in_progress", "blocked")),
        Task.due_date < now,
    )
    decision_filters = (
        Decision.created_at >= period_start,
        Decision.created_at < now,
        func.coalesce(Decision.extra_metadata["superseded"].as_boolean(), False).is_(False),
    )

    risk_count = await session.scalar(select(func.count(Risk.id)).where(*risk_filters))
    task_count = await session.scalar(select(func.count(Task.id)).where(*task_filters))
    decision_count = await session.scalar(select(func.count(Decision.id)).where(*decision_filters))

    risks = (
        await session.execute(
            select(Risk, Project.name)
            .outerjoin(Project, Risk.project_id == Project.id)
            .where(*risk_filters)
            .order_by(
                case(
                    {"critical": 0, "high": 1, "medium": 2, "low": 3}, value=Risk.severity, else_=4
                ),
                Risk.created_at.asc(),
                Risk.id.asc(),
            )
            .limit(HIGHLIGHT_LIMIT)
        )
    ).all()
    tasks = (
        await session.execute(
            select(Task, Project.name)
            .outerjoin(Project, Task.project_id == Project.id)
            .where(*task_filters)
            .order_by(
                case({"urgent": 0, "high": 1, "medium": 2, "low": 3}, value=Task.priority, else_=4),
                Task.due_date.asc(),
                Task.id.asc(),
            )
            .limit(HIGHLIGHT_LIMIT)
        )
    ).all()
    decisions = (
        await session.execute(
            select(Decision, Project.name)
            .outerjoin(Project, Decision.project_id == Project.id)
            .where(*decision_filters)
            .order_by(Decision.created_at.desc(), Decision.id.asc())
            .limit(HIGHLIGHT_LIMIT)
        )
    ).all()

    def evidence(row, project_name):
        return {
            "id": row.id,
            "project_id": row.project_id,
            "project_name": project_name,
            "transcript_id": row.transcript_id,
            "source_quote": row.source_quote,
            "confidence": row.confidence,
        }

    return WeeklySummary(
        generated_at=now,
        period_start=period_start,
        period_end=now,
        open_risk_count=int(risk_count or 0),
        overdue_task_count=int(task_count or 0),
        decision_count=int(decision_count or 0),
        open_risks=[
            SummaryRisk(
                **evidence(row, name),
                title=row.title,
                description=row.description,
                severity=row.severity,
                mitigation=row.mitigation,
            )
            for row, name in risks
        ],
        overdue_tasks=[
            SummaryTask(
                **evidence(row, name),
                title=row.title,
                owner=row.owner,
                priority=row.priority,
                status=row.status,
                due_date=row.due_date,
            )
            for row, name in tasks
        ],
        decisions=[
            SummaryDecision(
                **evidence(row, name),
                summary=row.summary,
                rationale=row.rationale,
                decided_by=row.decided_by,
                created_at=row.created_at,
            )
            for row, name in decisions
        ],
    )
