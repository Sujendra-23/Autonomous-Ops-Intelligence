"""Ingest -> tables -> masked views on real PostgreSQL (opt-in via ANALYTICS_TEST_ADMIN_URL)."""

from __future__ import annotations

from app.ingest import github_metrics as gm
from app.services.analytics import validate_sql
from tests.test_analytics_postgres import analytics_db  # noqa: F401
from tests.test_github_metrics import LONG_AGO, fake_github


class RollbackSafeConnection:
    """Lends the fixture's cursor to ``save`` without committing the fixture transaction."""

    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._connection.cursor()


def test_ingest_is_idempotent_and_feeds_the_masked_views(analytics_db):  # noqa: F811
    client, _ = fake_github()
    prs = gm.fetch_pull_requests(client, "o/r", LONG_AGO)
    runs = gm.fetch_workflow_runs(client, "o/r", "release-checks.yml", LONG_AGO)
    connection = RollbackSafeConnection(analytics_db)
    for _ in range(2):  # a re-run must update in place, not duplicate
        gm.save(connection, prs, runs)
    with analytics_db.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.pull_requests")
        assert cursor.fetchone() == (len(prs),)
        cursor.execute("SELECT count(*) FROM public.deployments")
        assert cursor.fetchone() == (len(runs),)

        cursor.execute("SET LOCAL ROLE aoi_analytics_masked")
        cursor.execute(
            validate_sql("SELECT state, COUNT(*) FROM analytics.pull_requests GROUP BY state")
        )
        assert dict(cursor.fetchall()) == {"merged": 4, "closed": 1, "open": 4}
        cursor.execute(validate_sql("SELECT DISTINCT author FROM analytics.pull_requests"))
        assert cursor.fetchall() == [("[MASKED]",)]
        cursor.execute(
            validate_sql(
                "SELECT SUM(deployment_count), SUM(failed_deployment_count) "
                "FROM analytics.dora_weekly"
            )
        )
        deployed, failed = cursor.fetchone()
        assert deployed == sum(r.status == "success" for r in runs)
        assert failed == sum(r.status == "failure" for r in runs)
