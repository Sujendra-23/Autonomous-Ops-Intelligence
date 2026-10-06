"""Opt-in real PostgreSQL ACL/view checks, fully rolled back.

ANALYTICS_TEST_ADMIN_URL must name an EMPTY disposable database on an isolated
Postgres cluster, with a superuser connection. Never point this at application data.
"""

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
            path = Path(__file__).parents[1] / "alembic/versions/0003_analytics.py"
            spec = importlib.util.spec_from_file_location("analytics_migration", path)
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
