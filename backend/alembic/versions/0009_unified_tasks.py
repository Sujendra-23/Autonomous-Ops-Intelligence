"""Native task origins, external identities, and resumable tenant-scoped imports."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0009_unified_tasks"
down_revision = "0008_multilingual_listening"
branch_labels = depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("source", sa.String(16), nullable=False,
                                    server_default="meeting"))
    op.add_column("tasks", sa.Column("external_scope", sa.String(64)))
    op.add_column("tasks", sa.Column("external_id", sa.String(128)))
    op.create_unique_constraint("uq_tasks_external_identity", "tasks",
                                ["workspace_id", "source", "external_scope", "external_id"])
    op.create_table(
        "task_import_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"),
                  nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("cursor", sa.Text()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("workspace_id", "provider", name="uq_task_import_workspace_provider"),
    )
    op.create_index("ix_task_import_states_workspace_id", "task_import_states", ["workspace_id"])
    op.execute('ALTER TABLE task_import_states ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE task_import_states FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY workspace_isolation ON task_import_states
        USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)""")


def downgrade():
    op.drop_table("task_import_states")
    op.drop_constraint("uq_tasks_external_identity", "tasks", type_="unique")
    for column in ("external_id", "external_scope", "source"):
        op.drop_column("tasks", column)
