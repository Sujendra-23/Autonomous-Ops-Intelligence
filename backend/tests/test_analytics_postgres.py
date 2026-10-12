"""Opt-in real PostgreSQL ACL/view checks, fully rolled back.

ANALYTICS_TEST_ADMIN_URL must name an EMPTY disposable database on an isolated
Postgres cluster, with a superuser connection. Never point this at application data.
"""

import datetime as dt
import importlib.util
import os
from pathlib import Path

import psycopg2
import pytest

from app.services.analytics import CATALOG, validate_sql


@pytest.fixture
def analytics_db():
    url = os.environ.get("ANALYTICS_TEST_ADMIN_URL")
    if not url:
        if os.environ.get("REQUIRE_DATABASE_TESTS") == "true":
            pytest.fail("ANALYTICS_TEST_ADMIN_URL is required for release checks")
        pytest.skip("Set ANALYTICS_TEST_ADMIN_URL to an isolated empty PostgreSQL database")
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            assert cursor.fetchone()[0] == 0, "Analytics test database must be empty"
            cursor.execute("""
                CREATE TABLE public.tasks (
                    id uuid, project_id uuid, status text, priority text,
                    owner text, due_date timestamptz
                );
                CREATE TABLE public.risks (
                    id uuid, project_id uuid, status text, severity text, likelihood text
                );
                CREATE TABLE public.blockers (
                    id uuid, project_id uuid, task_id uuid, status text,
                    severity text, resolved_at timestamptz
                );
                INSERT INTO public.tasks VALUES
                    ('00000000-0000-0000-0000-000000000001', NULL,
                     'open', 'high', 'Ada', now() - interval '1 day'),
                    ('00000000-0000-0000-0000-000000000002', NULL,
                     'done', 'low', 'bo@example.test', now() - interval '1 day'),
                    ('00000000-0000-0000-0000-000000000003', NULL,
                     'open', 'medium', NULL, NULL);
            """)
            versions = Path(__file__).parents[1] / "alembic/versions"
            for name in ("0003_analytics.py", "0007_developer_metrics.py"):
                spec = importlib.util.spec_from_file_location(name[:-3], versions / name)
                migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration)

                class Operations:
                    execute = staticmethod(cursor.execute)

                migration.op = Operations()
                migration.upgrade()
            cursor.execute(
                (Path(__file__).parents[1] / "evals/provision_analytics.sql").read_text()
            )
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_postgres_views_roles_and_masking(analytics_db):
    with analytics_db.cursor() as cursor:
        for view, columns in CATALOG.items():
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'analytics_masked' AND table_name = %s "
                "ORDER BY ordinal_position",
                (view,),
            )
            assert [row[0] for row in cursor.fetchall()] == list(columns)
        cursor.execute("SET LOCAL ROLE aoi_analytics_masked")
        cursor.execute(validate_sql("SELECT owner FROM analytics.tasks ORDER BY id"))
        assert cursor.fetchall() == [("[MASKED]",), ("[MASKED]",), (None,)]
        cursor.execute(validate_sql("SELECT COUNT(*) FROM analytics.tasks WHERE is_overdue"))
        assert cursor.fetchone() == (1,)
        cursor.execute(validate_sql("SELECT MAX(owner) AS email FROM analytics.tasks"))
        assert cursor.fetchone() == ("[MASKED]",)
        cursor.execute(validate_sql("SELECT COUNT(*) FROM analytics.tasks WHERE owner = 'Ada'"))
        assert cursor.fetchone() == (0,)
        for denied in (
            "SELECT owner FROM public.tasks",
            "SELECT owner FROM analytics_owner.tasks",
            "UPDATE analytics_masked.tasks SET status = 'done'",
            "CREATE TABLE analytics_masked.stolen (id int)",
        ):
            cursor.execute("SAVEPOINT denied")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(denied)
            cursor.execute("ROLLBACK TO SAVEPOINT denied")
        cursor.execute("RESET ROLE")
        cursor.execute("SET LOCAL ROLE aoi_analytics_owner")
        cursor.execute(
            validate_sql("SELECT owner FROM analytics.tasks ORDER BY id", read_owners=True)
        )
        assert cursor.fetchall() == [("Ada",), ("bo@example.test",), (None,)]
        cursor.execute("SAVEPOINT owner_write")
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cursor.execute("DELETE FROM analytics_owner.tasks")
        cursor.execute("ROLLBACK TO SAVEPOINT owner_write")


def _seed_developer_metrics(cursor):
    cursor.execute("""
        INSERT INTO public.pull_requests
            (repo, number, author_login, state, created_at, first_review_at, merged_at) VALUES
            ('a/x', 1, 'ada-dev', 'merged', '2026-01-05 09:00+00', '2026-01-05 10:00+00',
             '2026-01-05 17:00+00'),
            ('a/x', 2, 'bo-dev', 'merged', '2026-01-06 09:00+00', '2026-01-06 15:00+00',
             '2026-01-07 09:00+00'),
            ('a/x', 3, 'cy-dev', 'open', '2026-01-12 09:00+00', NULL, NULL),
            ('a/x', 4, NULL, 'closed', '2026-01-12 10:00+00', NULL, NULL),
            ('a/x', 5, 'ada-dev', 'merged', '2026-01-20 09:00+00', NULL, '2026-01-20 12:00+00');
        INSERT INTO public.deployments
            (repo, source, external_id, name, environment, status, started_at, finished_at) VALUES
            ('a/x', 'actions', 1, 'deploy', 'production', 'success',
             '2026-01-06 09:50+00', '2026-01-06 10:00+00'),
            ('a/x', 'actions', 2, 'deploy', 'production', 'failure',
             '2026-01-07 09:50+00', '2026-01-07 10:00+00'),
            ('a/x', 'actions', 3, 'deploy', 'production', 'failure',
             '2026-01-07 11:50+00', '2026-01-07 12:00+00'),
            ('a/x', 'actions', 4, 'deploy', 'production', 'success',
             '2026-01-08 08:50+00', '2026-01-08 09:00+00'),
            ('a/x', 'actions', 5, 'deploy', 'production', 'success',
             '2026-01-13 09:50+00', '2026-01-13 10:00+00'),
            ('a/x', 'actions', 6, 'deploy', 'staging', 'success',
             '2026-01-13 08:50+00', '2026-01-13 09:00+00'),
            ('a/x', 'actions', 7, 'deploy', 'production', 'other',
             '2026-01-14 09:50+00', '2026-01-14 10:00+00'),
            ('a/x', 'actions', 8, 'deploy', 'production', 'failure',
             '2026-01-20 09:50+00', '2026-01-20 10:00+00');
    """)


def test_postgres_developer_metrics_views(analytics_db):
    with analytics_db.cursor() as cursor:
        _seed_developer_metrics(cursor)
        cursor.execute("SET LOCAL ROLE aoi_analytics_masked")

        def rows(sql, **kwargs):
            cursor.execute(validate_sql(sql, **kwargs))
            return cursor.fetchall()

        # Lead time: PR opened -> first successful production deploy started after the merge.
        # PR 5 merged after the last success, so it has none; open/closed PRs have none.
        assert rows(
            "SELECT lead_time_hours FROM analytics.pull_requests "
            "WHERE is_merged = TRUE ORDER BY merged_at"
        ) == [(25.0,), (48.0,), (None,)]
        assert rows("SELECT AVG(lead_time_hours) FROM analytics.pull_requests") == [(36.5,)]
        assert rows(
            "SELECT hours_to_first_review, hours_to_merge FROM analytics.pull_requests "
            "WHERE state = 'merged' ORDER BY created_at LIMIT 2"
        ) == [(1.0, 8.0), (6.0, 24.0)]
        # Author is masked; filtering by the real login finds nothing.
        assert rows("SELECT DISTINCT author FROM analytics.pull_requests ORDER BY author") == [
            ("[MASKED]",),
            (None,),
        ]
        assert rows("SELECT COUNT(*) FROM analytics.pull_requests WHERE author = 'ada-dev'") == [
            (0,)
        ]
        # Change failure rate: failures / (successes + failures); 'other' and staging excluded.
        assert rows("SELECT AVG(failed) FROM analytics.deployments WHERE is_production = TRUE") == [
            (0.5,)
        ]
        # Consecutive failures are one incident; the unresolved one has no restore time.
        assert rows(
            "SELECT hours_to_restore FROM analytics.deployments "
            "WHERE is_failure = TRUE ORDER BY finished_at"
        ) == [(23.0,), (None,), (None,)]
        assert rows(
            "SELECT week_start, deployment_count, failed_deployment_count, change_failure_rate, "
            "median_lead_time_hours, restored_incident_count, median_time_to_restore_hours "
            "FROM analytics.dora_weekly ORDER BY week_start"
        ) == [
            (dt.date(2026, 1, 5), 2, 2, 0.5, 36.5, 1, 23.0),
            (dt.date(2026, 1, 12), 1, 0, 0.0, None, 0, None),
            (dt.date(2026, 1, 19), 0, 1, 1.0, None, 0, None),
        ]
        for denied in (
            "SELECT author_login FROM public.pull_requests",
            "SELECT author FROM analytics_owner.pull_requests",
            "SELECT * FROM public.deployments",
            "CREATE TABLE analytics_masked.stolen_dev (id int)",
        ):
            cursor.execute("SAVEPOINT denied")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(denied)
            cursor.execute("ROLLBACK TO SAVEPOINT denied")
        cursor.execute("RESET ROLE")
        cursor.execute("SET LOCAL ROLE aoi_analytics_owner")
        cursor.execute(
            validate_sql(
                "SELECT DISTINCT author FROM analytics.pull_requests ORDER BY author",
                read_owners=True,
            )
        )
        assert cursor.fetchall() == [("ada-dev",), ("bo-dev",), ("cy-dev",), (None,)]
