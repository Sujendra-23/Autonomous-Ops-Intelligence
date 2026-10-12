"""MCP server tests. The database layer is mocked; validation and rewriting are real."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from app import mcp_server
from app.config import Settings
from app.services.analytics import CATALOG

BACKEND = Path(__file__).parents[1]


@pytest.fixture
def settings(monkeypatch) -> Settings:
    settings = Settings(
        _env_file=None,
        intelligence_database_url="postgresql://masked.example/db",
        intelligence_owner_database_url="postgresql://owner.example/db",
    )
    monkeypatch.setattr("app.mcp_server.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.analytics.get_settings", lambda: settings)
    return settings


class FakeDatabase:
    """Stands in for the async engine; records what the executor sent."""

    def __init__(self, monkeypatch, rows=None, role="aoi_analytics_masked"):
        self.urls: list[str] = []
        self.statements: list[str] = []
        self.compiled: list[str] = []
        identity = {
            "name": role,
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": False,
        }
        connection = MagicMock()
        connection.execute = AsyncMock(side_effect=self._execute)
        connection.execute.return_value = MagicMock()
        connection.execute.return_value.mappings.return_value.one.return_value = identity
        result = MagicMock()
        result.keys.return_value = ["status", "n"]
        result.fetchmany.return_value = rows if rows is not None else [["open", 2]]
        connection.exec_driver_sql = AsyncMock(
            side_effect=lambda sql: self.compiled.append(sql) or result
        )
        connection.begin.return_value.__aenter__ = AsyncMock()
        connection.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        engine = MagicMock()
        engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
        engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        engine.dispose = AsyncMock()
        self.engine = engine
        self.connection = connection

        def create_engine(url, **_):
            self.urls.append(url)
            return engine

        monkeypatch.setattr("app.services.analytics.create_async_engine", create_engine)

    async def _execute(self, statement):
        self.statements.append(str(statement))
        return self.connection.execute.return_value


def test_list_views_matches_catalog(settings):
    result = mcp_server.list_views()
    assert [v["name"] for v in result["views"]] == [f"analytics.{v}" for v in CATALOG]
    assert result["owners_masked"] is True
    assert all(v["columns"] == len(CATALOG[v["name"].split(".")[1]]) for v in result["views"])


@pytest.mark.parametrize("name", ["tasks", "analytics.tasks"])
def test_describe_view(settings, name):
    result = mcp_server.describe_view(name)
    assert result["view"] == "analytics.tasks"
    assert [c["name"] for c in result["columns"]] == list(CATALOG["tasks"])
    assert result["columns"][0]["description"] == CATALOG["tasks"]["id"]


@pytest.mark.parametrize("name", ["", "public.tasks", "pg_roles", "analytics_owner.tasks"])
def test_describe_unknown_view(settings, name):
    with pytest.raises(ToolError, match="Unknown view"):
        mcp_server.describe_view(name)


async def test_run_query_defaults_to_masked(settings, monkeypatch):
    db = FakeDatabase(monkeypatch)
    result = await mcp_server.run_query(
        "SELECT status, COUNT(*) AS n FROM analytics.tasks GROUP BY status"
    )
    assert result == {
        "columns": ["status", "n"],
        "rows": [["open", 2]],
        "truncated": False,
        "owners_masked": True,
    }
    assert db.urls == ["postgresql+asyncpg://masked.example/db"]
    assert "analytics_masked.tasks" in db.compiled[0]
    assert "analytics_owner" not in db.compiled[0]
    assert db.statements[0] == "SET TRANSACTION READ ONLY"


async def test_owner_access_needs_explicit_flag(settings, monkeypatch):
    settings.mcp_allow_owner_access = True
    db = FakeDatabase(monkeypatch, role="aoi_analytics_owner")
    result = await mcp_server.run_query("SELECT owner FROM analytics.tasks")
    assert result["owners_masked"] is False
    assert db.urls == ["postgresql+asyncpg://owner.example/db"]
    assert "analytics_owner.tasks" in db.compiled[0]


async def test_owner_flag_without_owner_credentials_fails_closed(settings, monkeypatch):
    settings.mcp_allow_owner_access = True
    settings.intelligence_owner_database_url = SecretStr("")
    FakeDatabase(monkeypatch)
    with pytest.raises(ToolError, match="not configured"):
        await mcp_server.run_query("SELECT owner FROM analytics.tasks")


async def test_owner_flag_does_not_fall_back_to_masked_url_role(settings, monkeypatch):
    # Owner mode connecting as the masked role must be refused by the role check.
    settings.mcp_allow_owner_access = True
    db = FakeDatabase(monkeypatch, role="aoi_analytics_masked")
    with pytest.raises(ToolError, match="restricted role"):
        await mcp_server.run_query("SELECT owner FROM analytics.tasks")
    assert db.compiled == []


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM analytics.tasks",
        "SELECT * FROM public.tasks",
        "SELECT * FROM analytics_owner.tasks",
        "SELECT pg_sleep(10) FROM analytics.tasks",
        "SELECT * FROM analytics.tasks a JOIN analytics.risks b ON a.id = b.id",
        "SELECT * FROM analytics.tasks; DROP TABLE tasks",
    ],
)
async def test_unsafe_sql_is_rejected_before_the_database(settings, monkeypatch, sql):
    db = FakeDatabase(monkeypatch)
    with pytest.raises(ToolError, match="Query rejected"):
        await mcp_server.run_query(sql)
    assert db.urls == []


async def test_unconfigured_database(settings, monkeypatch):
    settings.intelligence_database_url = SecretStr("")
    with pytest.raises(ToolError, match="database is not configured"):
        await mcp_server.run_query("SELECT COUNT(*) FROM analytics.tasks")


async def test_driver_errors_are_not_leaked(settings, monkeypatch):
    db = FakeDatabase(monkeypatch)
    db.connection.exec_driver_sql = AsyncMock(
        side_effect=OperationalError("SELECT secret", {}, Exception("host=10.0.0.5 pw=hunter2"))
    )
    with pytest.raises(ToolError) as caught:
        await mcp_server.run_query("SELECT COUNT(*) FROM analytics.tasks")
    assert str(caught.value) == "Query could not be completed"


async def test_values_are_json_safe_and_truncation_reported(settings, monkeypatch):
    ident = uuid.UUID("00000000-0000-0000-0000-000000000001")
    when = dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.UTC)
    FakeDatabase(monkeypatch, rows=[[ident, when]] * 201)
    result = await mcp_server.run_query("SELECT id, due_date FROM analytics.tasks")
    assert result["truncated"] is True
    assert len(result["rows"]) == 200
    assert result["rows"][0] == [str(ident), str(when)]


async def test_protocol_round_trip_in_memory(settings, monkeypatch):
    FakeDatabase(monkeypatch)
    async with create_connected_server_and_client_session(mcp_server.mcp._mcp_server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"list_views", "describe_view", "run_query"}
        ok = await client.call_tool(
            "run_query",
            {"sql": "SELECT status, COUNT(*) AS n FROM analytics.tasks GROUP BY status"},
        )
        assert not ok.isError
        assert ok.structuredContent["rows"] == [["open", 2]]
        bad = await client.call_tool("run_query", {"sql": "DROP TABLE tasks"})
        assert bad.isError
        resources = await client.list_resources()
        assert [str(r.uri) for r in resources.resources] == ["aoi://analytics/catalog"]


async def test_stdio_server_starts_and_enforces_validation(tmp_path):
    """Spawn the real entrypoint over stdio; no database is configured or reachable."""
    env = {
        "PATH": "/usr/bin:/bin",
        "INTELLIGENCE_DATABASE_URL": "",
        "INTELLIGENCE_OWNER_DATABASE_URL": "",
        "MCP_ALLOW_OWNER_ACCESS": "false",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server"], cwd=BACKEND, env=env
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert names == {"list_views", "describe_view", "run_query"}
        views = await session.call_tool("list_views", {})
        assert views.structuredContent["owners_masked"] is True
        rejected = await session.call_tool("run_query", {"sql": "SELECT * FROM public.tasks"})
        assert rejected.isError and "Query rejected" in rejected.content[0].text
        offline = await session.call_tool(
            "run_query", {"sql": "SELECT COUNT(*) FROM analytics.tasks"}
        )
        assert offline.isError and "not configured" in offline.content[0].text
