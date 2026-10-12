"""Audit trail for data-subject requests; deliberately holds no personal data."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models._mixins import UUIDPrimaryKey


class DataSubjectAudit(UUIDPrimaryKey, Base):
    """One row per completed erasure request.

    There is intentionally no account or workspace column: the rows it describes no longer
    exist. ``subject_digest`` is a keyed digest that lets an operator confirm a given account
    was erased without the table being a list of who asked.
    """

    __tablename__ = "data_subject_audit"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    subject_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Counts only, keyed by table name; never identifiers or content.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
