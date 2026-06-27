"""API schemas for the transcript endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TranscriptCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=20)
    source: str = "upload"
    meeting_date: datetime | None = None
    participants: list[str] | None = None
    project_hint: str | None = Field(
        None,
        description="Optional project name to bias the extractor.",
    )
    sync_extract: bool = Field(
        default=True,
        description=(
            "If true (default), run extraction in-process before returning. "
            "Set false for fire-and-forget ingestion (still extracted by the worker)."
        ),
    )


class TranscriptSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: str
    source: str
    project_id: uuid.UUID | None
    meeting_date: datetime | None
    processed_at: datetime | None
    created_at: datetime


class TranscriptList(BaseModel):
    items: list[TranscriptSummary]
    total: int


class ExtractedTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    owner: str | None
    due_date: datetime | None
    status: str
    priority: str
    source_quote: str | None
    confidence: float | None


class ExtractedDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    summary: str
    rationale: str | None
    decided_by: list[str] | None
    source_quote: str | None


class ExtractedRiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    severity: str
    likelihood: str
    mitigation: str | None
    source_quote: str | None


class ExtractedBlockerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    summary: str
    blocked_party: str | None
    needs_from: str | None
    severity: str
    status: str


class TranscriptDetail(TranscriptSummary):
    content: str
    participants: list[str] | None
    error: str | None
    tasks: list[ExtractedTaskOut] = []
    decisions: list[ExtractedDecisionOut] = []
    risks: list[ExtractedRiskOut] = []
    blockers: list[ExtractedBlockerOut] = []
