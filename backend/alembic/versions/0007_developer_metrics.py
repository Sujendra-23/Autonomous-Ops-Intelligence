"""Pull request and deployment facts plus masked/owner developer-metrics views.

Plain ``op.execute`` DDL on purpose: the Postgres analytics test replays ``upgrade()`` through a
cursor. These tables hold engineering telemetry from GitHub, not meeting data, so they are
organization-wide (no workspace column / RLS), like the analytics views themselves. Role grants
stay an explicit DBA step (evals/provision_analytics.sql, evals/grant_developer_metrics.sql).
"""

from alembic import op

revision = "0007_developer_metrics"
down_revision = "0006_merge_heads"
branch_labels = None
depends_on = None

SCHEMAS = ("analytics_masked", "analytics_owner")
VIEWS = ("dora_weekly", "deployments", "pull_requests")  # drop order


def _pull_requests_view(schema: str) -> str:
    author = (
        "p.author_login"
        if schema == "analytics_owner"
        else "CASE WHEN NULLIF(BTRIM(p.author_login), '') IS NULL THEN NULL "
        "ELSE '[MASKED]'::text END"
    )
    return f"""
        CREATE VIEW {schema}.pull_requests WITH (security_barrier=true) AS
        SELECT
            p.id,
            p.repo,
            p.state,
            {author} AS author,
            NULLIF(BTRIM(p.author_login), '') IS NOT NULL AS has_author,
            p.created_at,
            p.first_review_at,
            p.merged_at,
            p.merged_at IS NOT NULL AS is_merged,
            EXTRACT(EPOCH FROM (p.first_review_at - p.created_at)) / 3600.0
                AS hours_to_first_review,
            EXTRACT(EPOCH FROM (p.merged_at - p.created_at)) / 3600.0 AS hours_to_merge,
            d.finished_at AS deployed_at,
            CAST(DATE_TRUNC('week', d.finished_at AT TIME ZONE 'UTC') AS date)
                AS deployed_week_start,
            EXTRACT(EPOCH FROM (d.finished_at - p.created_at)) / 3600.0 AS lead_time_hours
        FROM public.pull_requests p
        LEFT JOIN LATERAL (
            SELECT x.finished_at FROM public.deployments x
            WHERE x.repo = p.repo AND x.environment = 'production' AND x.status = 'success'
              AND p.merged_at IS NOT NULL AND x.started_at >= p.merged_at
              AND x.finished_at IS NOT NULL
            ORDER BY x.finished_at LIMIT 1
        ) d ON true
    """


def _deployments_view(schema: str) -> str:
    return f"""
        CREATE VIEW {schema}.deployments WITH (security_barrier=true) AS
        SELECT
            d.id,
            d.repo,
            d.environment,
            d.name AS workflow,
            d.status,
            d.started_at,
            d.finished_at,
            d.environment = 'production' AS is_production,
            d.status = 'failure' AS is_failure,
            CASE WHEN d.status = 'success' THEN 0 WHEN d.status = 'failure' THEN 1 END
                AS failed,
            CAST(DATE_TRUNC('week', d.finished_at AT TIME ZONE 'UTC') AS date)
                AS deployed_week_start,
            i.restored_at,
            EXTRACT(EPOCH FROM (i.restored_at - d.finished_at)) / 3600.0
                AS hours_to_restore
        FROM public.deployments d
        LEFT JOIN LATERAL (
            -- An incident starts at a failed deployment whose previous finished deployment
            -- (same repo and environment) did not fail; it ends at the next success.
            SELECT MIN(n.finished_at) AS restored_at FROM public.deployments n
            WHERE d.status = 'failure' AND n.repo = d.repo AND n.environment = d.environment
              AND n.status = 'success' AND n.finished_at > d.finished_at
              AND COALESCE((
                  SELECT q.status FROM public.deployments q
                  WHERE q.repo = d.repo AND q.environment = d.environment
                    AND q.status IN ('success', 'failure') AND q.finished_at < d.finished_at
                  ORDER BY q.finished_at DESC LIMIT 1
              ), 'success') = 'success'
        ) i ON true
    """


def _dora_weekly_view(schema: str) -> str:
    return f"""
        CREATE VIEW {schema}.dora_weekly WITH (security_barrier=true) AS
        WITH dep AS (
            SELECT repo, deployed_week_start AS week_start,
                   COUNT(*) FILTER (WHERE status = 'success') AS deployment_count,
                   COUNT(*) FILTER (WHERE status = 'failure') AS failed_deployment_count
            FROM {schema}.deployments
            WHERE is_production AND status IN ('success', 'failure')
              AND deployed_week_start IS NOT NULL
            GROUP BY repo, deployed_week_start
        ), lead AS (
            SELECT repo, deployed_week_start AS week_start,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY lead_time_hours)
                       AS median_lead_time_hours
            FROM {schema}.pull_requests
            WHERE lead_time_hours IS NOT NULL
            GROUP BY repo, deployed_week_start
        ), restore AS (
            SELECT repo,
                   CAST(DATE_TRUNC('week', restored_at AT TIME ZONE 'UTC') AS date)
                       AS week_start,
                   COUNT(*) AS restored_incident_count,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY hours_to_restore)
                       AS median_time_to_restore_hours
            FROM {schema}.deployments
            WHERE is_production AND hours_to_restore IS NOT NULL
            GROUP BY repo, CAST(DATE_TRUNC('week', restored_at AT TIME ZONE 'UTC') AS date)
        )
        SELECT
            COALESCE(dep.repo, lead.repo, restore.repo) AS repo,
            COALESCE(dep.week_start, lead.week_start, restore.week_start) AS week_start,
            COALESCE(dep.deployment_count, 0) AS deployment_count,
            COALESCE(dep.failed_deployment_count, 0) AS failed_deployment_count,
            CASE WHEN COALESCE(dep.deployment_count, 0)
                      + COALESCE(dep.failed_deployment_count, 0) > 0
                 THEN dep.failed_deployment_count::double precision
                      / (dep.deployment_count + dep.failed_deployment_count) END
                AS change_failure_rate,
            lead.median_lead_time_hours,
            COALESCE(restore.restored_incident_count, 0) AS restored_incident_count,
            restore.median_time_to_restore_hours
        FROM dep
        FULL JOIN lead ON lead.repo = dep.repo AND lead.week_start = dep.week_start
        FULL JOIN restore
            ON restore.repo = COALESCE(dep.repo, lead.repo)
           AND restore.week_start = COALESCE(dep.week_start, lead.week_start)
    """


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.pull_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            repo text NOT NULL,
            number integer NOT NULL,
            author_login text,
            state text NOT NULL CHECK (state IN ('open', 'closed', 'merged')),
            created_at timestamptz NOT NULL,
            first_review_at timestamptz,
            merged_at timestamptz,
            closed_at timestamptz,
            merge_commit_sha text,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_pull_requests_repo_number UNIQUE (repo, number)
        )
    """)
    op.execute("""
        CREATE TABLE public.deployments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            repo text NOT NULL,
            source text NOT NULL CHECK (source IN ('actions', 'deployments')),
            external_id bigint NOT NULL,
            name text,
            environment text NOT NULL DEFAULT 'production',
            sha text,
            status text NOT NULL CHECK (status IN ('success', 'failure', 'other')),
            started_at timestamptz,
            finished_at timestamptz,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_deployments_repo_source_external UNIQUE (repo, source, external_id)
        )
    """)
    op.execute("CREATE INDEX ix_deployments_repo_env_finished ON public.deployments "
               "(repo, environment, finished_at)")
    op.execute("CREATE INDEX ix_pull_requests_repo_merged ON public.pull_requests "
               "(repo, merged_at)")
    for schema in SCHEMAS:
        op.execute(_pull_requests_view(schema))
        op.execute(_deployments_view(schema))
        op.execute(_dora_weekly_view(schema))
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC")
    op.execute("REVOKE ALL ON public.pull_requests, public.deployments FROM PUBLIC")
    op.execute(
        "COMMENT ON COLUMN analytics_masked.pull_requests.author IS "
        "'GitHub login; masked unless intelligence:read_owners is granted'"
    )


def downgrade() -> None:
    for schema in SCHEMAS:
        for view in VIEWS:
            op.execute(f"DROP VIEW {schema}.{view}")
    op.execute("DROP TABLE public.deployments")
    op.execute("DROP TABLE public.pull_requests")
