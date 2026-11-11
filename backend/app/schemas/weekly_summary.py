"""Bounded, source-backed leadership briefing from existing workspace records."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SummaryEvidence(BaseModel):
    id: UUID
    project_id: UUID | None
    project_name: str | None
    transcript_id: UUID | None
    source_quote: str | None
    confidence: float | None


class SummaryRisk(SummaryEvidence):
    title: str
    description: str | None
    severity: str
    mitigation: str | None


class SummaryTask(SummaryEvidence):
    title: str
    owner: str | None
    priority: str
    status: str
    due_date: datetime


class SummaryDecision(SummaryEvidence):
    summary: str
    rationale: str | None
    decided_by: list[str] | None
    created_at: datetime


class WeeklySummary(BaseModel):
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    open_risk_count: int
    overdue_task_count: int
    decision_count: int
    open_risks: list[SummaryRisk]
    overdue_tasks: list[SummaryTask]
    decisions: list[SummaryDecision]
