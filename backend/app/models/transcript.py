"""Transcript + per-chunk embedding storage."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, event, inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._mixins import TenantMixin, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.project import Project


EMBEDDING_DIMS = 1536  # OpenAI text-embedding-3-small / local fallback both project to this


class Transcript(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        CheckConstraint(
            "sensitivity_label IN ('public', 'internal', 'confidential')",
            name="ck_transcripts_sensitivity_label",
        ),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="upload")
    meeting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    participants: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    calendar_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity_label: Mapped[str] = mapped_column(
        String(16), nullable=False, default="internal", server_default="internal"
    )
    dlp_findings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    dlp_policy_version: Mapped[str] = mapped_column(
        String(64), default="regex-checksum-v1", server_default="regex-checksum-v1"
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    # received → chunking → extracting → completed | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project | None] = relationship(back_populates="transcripts")
    chunks: Mapped[list[TranscriptChunk]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="TranscriptChunk.index",
    )

    def __repr__(self) -> str:
        return f"<Transcript {self.title!r} status={self.status}>"


@event.listens_for(Transcript, "before_insert")
@event.listens_for(Transcript, "before_update")
def sanitize_transcript(mapper, connection, transcript):
    from app.services.dlp import RANK, protect_transcript

    previous = inspect(transcript).attrs.sensitivity_label.history.deleted
    if previous:
        transcript.sensitivity_label = max(
            (previous[0], transcript.sensitivity_label or "internal"), key=RANK.__getitem__
        )
    protect_transcript(transcript)


class TranscriptChunk(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "transcript_chunks"

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transcripts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMS), nullable=True)

    transcript: Mapped[Transcript] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<TranscriptChunk {self.transcript_id}:{self.index}>"
