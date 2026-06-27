"""Schemas describing the JSON the LLM emits and we persist.

These double as:
- the JSON schema we hand to the model so it can return structured output,
- the validation layer that catches malformed model output,
- the contract the API exposes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TaskExtraction(BaseModel):
    title: str = Field(..., max_length=512, description="Concise imperative task title.")
    description: str | None = Field(None, description="Additional context if needed.")
    owner: str | None = Field(
        None,
        description="Single named owner. None if no person was explicitly assigned.",
    )
    due_date: datetime | None = Field(
        None,
        description="Best-effort due date inferred from the conversation, ISO-8601.",
    )
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    source_quote: str = Field(
        ...,
        description="A verbatim snippet from the transcript supporting this extraction.",
        max_length=2000,
    )
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        return v.strip()


class DecisionExtraction(BaseModel):
    summary: str = Field(..., max_length=512)
    rationale: str | None = None
    decided_by: list[str] = Field(default_factory=list)
    source_quote: str = Field(..., max_length=2000)
    confidence: float = Field(..., ge=0.0, le=1.0)


class RiskExtraction(BaseModel):
    title: str = Field(..., max_length=512)
    description: str | None = None
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    likelihood: Literal["low", "medium", "high"] = "medium"
    mitigation: str | None = None
    source_quote: str = Field(..., max_length=2000)
    confidence: float = Field(..., ge=0.0, le=1.0)


class BlockerExtraction(BaseModel):
    summary: str = Field(..., max_length=512)
    description: str | None = None
    blocked_party: str | None = Field(None, description="Who is blocked.")
    needs_from: str | None = Field(None, description="Who/what is needed to unblock.")
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    source_quote: str = Field(..., max_length=2000)
    confidence: float = Field(..., ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    """Top-level structured output we ask the LLM to produce."""

    project_hint: str | None = Field(
        None,
        description="Best guess at the project this meeting concerns, or null if unclear.",
    )
    summary: str = Field(..., description="Two-to-four sentence executive summary.")
    tasks: list[TaskExtraction] = Field(default_factory=list)
    decisions: list[DecisionExtraction] = Field(default_factory=list)
    risks: list[RiskExtraction] = Field(default_factory=list)
    blockers: list[BlockerExtraction] = Field(default_factory=list)
