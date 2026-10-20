"""Independent reconciliation state for Notion task checkboxes."""

import sqlalchemy as sa
from alembic import op

revision = "0007_notion_sync"
down_revision = "0006_merge_heads"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "tasks", sa.Column(
            "notion_sync_pending", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("tasks", sa.Column("notion_sync_checked_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("notion_sync_error", sa.String(128)))
    op.create_index(
        "ix_tasks_notion_block", "tasks", ["workspace_id", "notion_block_id"], unique=True
    )


def downgrade():
    op.drop_index("ix_tasks_notion_block", table_name="tasks")
    for column in ("notion_sync_error", "notion_sync_checked_at", "notion_sync_pending"):
        op.drop_column("tasks", column)
