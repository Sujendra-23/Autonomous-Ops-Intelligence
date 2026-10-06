"""Accounts, encrypted workspace connectors, and forced tenant policies.

Existing data remains in a quarantined legacy workspace, never assigned on signup.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0004_workspaces"
down_revision = "0003_analytics"
branch_labels = depends_on = None
TABLES = (
    "projects",
    "transcripts",
    "transcript_chunks",
    "tasks",
    "task_activities",
    "decisions",
    "risks",
    "blockers",
    "webhook_deliveries",
)
LEGACY = "00000000-0000-0000-0000-000000000001"


def upgrade():
    from app.models.account import AccessToken, Account, Membership, Workspace

    for model in (Account, Workspace, Membership, AccessToken):
        model.__table__.create(op.get_bind())
    op.execute(
        sa.text(
            "INSERT INTO workspaces(id,name) VALUES (CAST(:id AS uuid),'Quarantined legacy data')"
        ).bindparams(id=LEGACY)
    )
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "workspace_id",
                UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text(f"'{LEGACY}'::uuid"),
            ),
        )
        op.alter_column(table, "workspace_id", server_default=None)
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
        op.create_foreign_key(
            f"fk_{table}_workspace", table, "workspaces", ["workspace_id"], ["id"]
        )
        op.create_unique_constraint(f"uq_{table}_workspace_id", table, ["workspace_id", "id"])
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY workspace_isolation ON "{table}"
            USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (workspace_id =
                NULLIF(current_setting('app.workspace_id', true), '')::uuid)""")
    # Existing installations have unique indexes, rather than named constraints.
    op.drop_constraint("projects_name_key", "projects", type_="unique")
    op.drop_constraint("projects_slug_key", "projects", type_="unique")
    op.drop_index("ix_projects_slug", table_name="projects")
    op.create_unique_constraint("uq_projects_workspace_name", "projects", ["workspace_id", "name"])
    op.create_unique_constraint("uq_projects_workspace_slug", "projects", ["workspace_id", "slug"])
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        for fk in inspector.get_foreign_keys(table):
            parent = fk["referred_table"]
            if parent in TABLES and fk["referred_columns"] == ["id"]:
                column = fk["constrained_columns"][0]
                op.create_foreign_key(
                    f"fk_{table}_{column}_tenant",
                    table,
                    parent,
                    ["workspace_id", column],
                    ["workspace_id", "id"],
                    deferrable=True,
                    initially="DEFERRED",
                )


def downgrade():
    raise RuntimeError("Workspace migration is irreversible; restore a verified backup instead")
