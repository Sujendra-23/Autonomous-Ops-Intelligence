"""Calendar context, task sync state, and durable webhook outbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_integrations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("calendar_context", postgresql.JSONB()))
    op.add_column(
        "tasks", sa.Column("sync_pending", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("tasks", sa.Column("sync_checked_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("sync_error", sa.String(128)))
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(128)),
    )
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    for column in ("sync_error", "sync_checked_at", "sync_pending"):
        op.drop_column("tasks", column)
    op.drop_column("transcripts", "calendar_context")
