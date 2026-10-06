"""Identity is verified by OIDC; membership and connector ownership live here."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models._mixins import Timestamps, UUIDPrimaryKey


class Account(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "accounts"
    subject: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)


class Workspace(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_ciphertext: Mapped[str | None] = mapped_column(Text)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner','admin','member','viewer')", name="valid_membership_role"
        ),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class AccessToken(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "access_tokens"
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
