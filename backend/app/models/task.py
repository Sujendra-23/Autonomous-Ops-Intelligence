"""Tasks extracted from transcripts and their activity log."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._mixins import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.transcript import Transcript


class TaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Task(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status_due", "status", "due_date"),
        Index("ix_tasks_owner", "owner"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transcripts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskStatus.OPEN.value)
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskPriority.MEDIUM.value
    )

    # Context surfaced from the originating transcript so humans can audit the LLM.
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    # External system mirrors
    linear_issue_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linear_issue_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jira_issue_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jira_issue_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notion_block_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    last_status_change_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    project: Mapped["Project | None"] = relationship(back_populates="tasks")
    transcript: Mapped["Transcript | None"] = relationship()
    activities: Mapped[list["TaskActivity"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskActivity.created_at",
    )

    def __repr__(self) -> str:
        return f"<Task {self.title!r} status={self.status}>"


class TaskActivity(UUIDPrimaryKey, Timestamps, Base):
    """Append-only audit log for every state change on a task."""

    __tablename__ = "task_activities"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "created", "status_change", "owner_assigned", "reminder_sent", "drift_detected"
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")

    task: Mapped["Task"] = relationship(back_populates="activities")
