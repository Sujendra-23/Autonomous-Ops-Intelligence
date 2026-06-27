"""Project — a unit of organizational work, linking transcripts to artefacts."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._mixins import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.blocker import Blocker
    from app.models.decision import Decision
    from app.models.risk import Risk
    from app.models.task import Task
    from app.models.transcript import Transcript


class Project(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    # External system identifiers — populated by integration adapters
    notion_page_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linear_project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    risks: Mapped[list["Risk"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    blockers: Mapped[list["Blocker"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project {self.slug}>"


# Helper exposed for typing in other modules
ProjectId = uuid.UUID
_ = UUID  # keep import for alembic autogen
