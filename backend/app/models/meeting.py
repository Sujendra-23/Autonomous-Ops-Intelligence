"""Workspace-owned scheduled calls and reusable audio huddles."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models._mixins import TenantMixin, Timestamps, UUIDPrimaryKey


class MeetingRoom(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "meeting_rooms"
    __table_args__ = (UniqueConstraint("workspace_id", "request_id"),)

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="meeting")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attendees: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    reminder_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=10)
    calendar_event_id: Mapped[str | None] = mapped_column(String(1024))
    calendar_url: Mapped[str | None] = mapped_column(Text)
    invitation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_requested"
    )

    @property
    def media_room(self) -> str:
        return f"aoi-{self.workspace_id}-{self.id}"
