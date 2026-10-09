"""Field-service job identifiers on tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0006_field_service_jobs"
down_revision = "0005_salesforce_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("field_service_job_id", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("field_service_job_url", sa.String(512), nullable=True))
    op.create_index("ix_tasks_field_service_job_id", "tasks", ["field_service_job_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_field_service_job_id", table_name="tasks")
    op.drop_column("tasks", "field_service_job_url")
    op.drop_column("tasks", "field_service_job_id")
