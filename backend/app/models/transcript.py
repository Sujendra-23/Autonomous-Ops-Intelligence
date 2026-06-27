"""Transcript + per-chunk embedding storage."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._mixins import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.project import Project


EMBEDDING_DIMS = 1536  # OpenAI text-embedding-3-small / local fallback both project to this


class Transcript(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "transcripts"

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
    content: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    # received → chunking → extracting → completed | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project | None"] = relationship(back_populates="transcripts")
    chunks: Mapped[list["TranscriptChunk"]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="TranscriptChunk.index",
    )

    def __repr__(self) -> str:
        return f"<Transcript {self.title!r} status={self.status}>"


class TranscriptChunk(UUIDPrimaryKey, Timestamps, Base):
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

    transcript: Mapped["Transcript"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<TranscriptChunk {self.transcript_id}:{self.index}>"
