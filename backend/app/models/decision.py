"""Decisions captured from meetings — the organisational memory backbone."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._mixins import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.transcript import Transcript


class Decision(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "decisions"

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

    summary: Mapped[str] = mapped_column(String(512), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    notion_block_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    project: Mapped["Project | None"] = relationship(back_populates="decisions")
    transcript: Mapped["Transcript | None"] = relationship()
