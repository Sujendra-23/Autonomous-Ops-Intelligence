"""Curated analytics views; role provisioning is an explicit DBA operation."""

from alembic import op

revision = "0003_analytics"
down_revision = "0002_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for schema in ("analytics_masked", "analytics_owner"):
        op.execute(f"CREATE SCHEMA {schema}")
        op.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
        owner = "owner" if schema == "analytics_owner" else (
            "CASE WHEN NULLIF(BTRIM(owner), '') IS NULL THEN NULL ELSE '[MASKED]'::text END"
        )
        op.execute(f"""
            CREATE VIEW {schema}.tasks WITH (security_barrier=true) AS
            SELECT id, project_id, status, priority, {owner} AS owner,
                   NULLIF(BTRIM(owner), '') IS NOT NULL AS has_owner, due_date,
                   COALESCE(due_date < CURRENT_TIMESTAMP AND
                       status IN ('open', 'in_progress', 'blocked'), false) AS is_overdue
            FROM public.tasks
        """)
        op.execute(f"""
            CREATE VIEW {schema}.risks WITH (security_barrier=true) AS
            SELECT id, project_id, status, severity, likelihood FROM public.risks
        """)
        op.execute(f"""
            CREATE VIEW {schema}.blockers WITH (security_barrier=true) AS
            SELECT id, project_id, task_id, status, severity, resolved_at FROM public.blockers
        """)
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC")
        op.execute(f"COMMENT ON COLUMN {schema}.tasks.owner IS "
                   "'Owner name or email; masked unless intelligence:read_owners is granted'")


def downgrade() -> None:
    for schema in ("analytics_masked", "analytics_owner"):
        for view in ("tasks", "risks", "blockers"):
            op.execute(f"DROP VIEW {schema}.{view}")
        op.execute(f"DROP SCHEMA {schema}")
