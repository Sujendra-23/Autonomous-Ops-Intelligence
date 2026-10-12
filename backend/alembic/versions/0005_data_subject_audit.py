"""Audit table for data-subject erasure requests (counts and a keyed digest only)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0005_data_subject_audit"
down_revision = "0004_workspaces"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "data_subject_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("details", JSONB, nullable=False),
    )
    op.create_index("ix_data_subject_audit_subject_digest", "data_subject_audit", ["subject_digest"])


def downgrade():
    op.drop_table("data_subject_audit")
