"""MCP tools against the real analytics views and roles on PostgreSQL (opt-in, rolled back).

Reuses the ``analytics_db`` fixture (empty disposable database, migration + provisioning SQL).
Only the connection layer is substituted: instead of opening a login connection, queries run
on the fixture's cursor under ``SET LOCAL ROLE`` for the matching restricted role, so view
rewriting, masking and grants are the real ones.
"""

from __future__ import annotations

import pytest

from app import mcp_server
from app.config import Settings
from app.services.analytics import MAX_ROWS, validate_sql
from tests.test_analytics_postgres import analytics_db  # noqa: F401


@pytest.fixture
def mcp_over_postgres(analytics_db, monkeypatch):  # noqa: F811
    settings = Settings(_env_file=None)
    monkeypatch.setattr("app.mcp_server.get_settings", lambda: settings)

    async def execute_as_role(sql, *, read_owners=False):
        compiled = validate_sql(sql, read_owners=read_owners)
        role = "aoi_analytics_owner" if read_owners else "aoi_analytics_masked"
        with analytics_db.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {role}")
            cursor.execute(compiled)
            columns = [d[0] for d in cursor.description]
            rows = [list(r) for r in cursor.fetchall()]
            cursor.execute("RESET ROLE")
        return {
            "columns": columns,
            "rows": rows[:MAX_ROWS],
            "truncated": len(rows) > MAX_ROWS,
            "owners_masked": not read_owners,
        }

    monkeypatch.setattr("app.mcp_server.execute_sql", execute_as_role)
    return settings


async def test_masked_by_default(mcp_over_postgres):
    result = await mcp_server.run_query("SELECT owner FROM analytics.tasks ORDER BY id")
    assert result["owners_masked"] is True
    assert result["rows"] == [["[MASKED]"], ["[MASKED]"], [None]]
    counted = await mcp_server.run_query("SELECT COUNT(*) FROM analytics.tasks WHERE is_overdue")
    assert counted["rows"] == [[1]]
    guess = await mcp_server.run_query("SELECT COUNT(*) FROM analytics.tasks WHERE owner = 'Ada'")
    assert guess["rows"] == [[0]]


async def test_owner_names_only_with_the_flag(mcp_over_postgres):
    mcp_over_postgres.mcp_allow_owner_access = True
    result = await mcp_server.run_query("SELECT owner FROM analytics.tasks ORDER BY id")
    assert result["owners_masked"] is False
    assert result["rows"] == [["Ada"], ["bo@example.test"], [None]]


async def test_every_catalog_column_is_selectable(mcp_over_postgres):
    for view in mcp_server.list_views()["views"]:
        columns = ", ".join(c["name"] for c in mcp_server.describe_view(view["name"])["columns"])
        sql = f"SELECT {columns} FROM {view['name']} LIMIT 1"  # noqa: S608 - catalog names
        result = await mcp_server.run_query(sql)
        assert result["columns"] == columns.split(", ")
