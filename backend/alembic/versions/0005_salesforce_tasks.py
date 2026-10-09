"""Salesforce Task identifiers on tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0005_salesforce_tasks"
down_revision = "0004_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("salesforce_task_id", sa.String(32), nullable=True))
    op.add_column("tasks", sa.Column("salesforce_task_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "salesforce_task_url")
    op.drop_column("tasks", "salesforce_task_id")
