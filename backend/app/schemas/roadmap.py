"""Validated planning input and editable, project-owned roadmaps."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonBlank = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class Milestone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    objective: NonBlank
    start_week: int = Field(ge=1, le=104)
    end_week: int = Field(ge=1, le=104)
    owner: str | None = Field(default=None, max_length=128)
    deliverables: list[Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")]] = Field(
        min_length=1, max_length=8
    )
    depends_on: list[int] = Field(default_factory=list, max_length=12)
    status: Literal["planned", "in_progress", "blocked", "done"] = "planned"
    task_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)


class RoadmapContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=3000, pattern=r"\S")
    assumptions: list[NonBlank] = Field(default_factory=list, max_length=10)
    milestones: list[Milestone] = Field(min_length=1, max_length=12)


class RoadmapDocument(RoadmapContent):
    horizon_weeks: int = Field(ge=1, le=104)
    start_date: date | None = None
    source_type: Literal["manual", "project", "transcript"]
    source_plan: str = Field(max_length=20000)
    source_meeting_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    generated_at: datetime

    @model_validator(mode="after")
    def validate_schedule(self):
        for number, milestone in enumerate(self.milestones, start=1):
            if not milestone.start_week <= milestone.end_week <= self.horizon_weeks:
                raise ValueError("Milestone weeks must fit within the roadmap duration")
            if len(set(milestone.depends_on)) != len(milestone.depends_on):
                raise ValueError("Milestone dependencies must be unique")
            for dependency in milestone.depends_on:
                if dependency < 1 or dependency >= number:
                    raise ValueError("Dependencies must reference an earlier milestone")
                if self.milestones[dependency - 1].end_week > milestone.start_week:
                    raise ValueError("A milestone cannot start before its dependency finishes")
        return self


class RoadmapGenerate(BaseModel):
    name: str = Field(min_length=1, max_length=256, pattern=r"\S")
    project_id: uuid.UUID | None = None
    transcript_id: uuid.UUID | None = None
    source_type: Literal["manual", "project", "transcript"] = "manual"
    plan: str = Field(default="", max_length=20000)
    horizon_weeks: int = Field(default=12, ge=1, le=104)
    start_date: date | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_type == "manual" and len(self.plan.strip()) < 20:
            raise ValueError("Describe the project plan in at least 20 characters")
        if self.source_type == "project" and self.project_id is None:
            raise ValueError("Choose a project to use its meetings")
        if self.source_type == "transcript" and self.transcript_id is None:
            raise ValueError("Choose a meeting transcript")
        return self


class RoadmapSave(BaseModel):
    project_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=256, pattern=r"\S")
    expected_version: int = Field(default=0, ge=0)
    roadmap: RoadmapDocument
    create_tasks: bool = False


class ProjectRoadmap(BaseModel):
    project_id: uuid.UUID
    name: str
    version: int
    roadmap: RoadmapDocument | None
    created_task_count: int = 0
