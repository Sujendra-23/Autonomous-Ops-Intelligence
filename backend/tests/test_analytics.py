from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.analytics import router
from app.config import Settings
from app.services.analytics import AnalyticsUnavailable, UnsafeQuery, execute_sql, validate_sql
from evals.run_analytics import DATA, fixture_connection


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM analytics.tasks",
        "SELECT * FROM analytics.tasks; SELECT * FROM analytics.tasks",
        "SELECT * FROM public.tasks",
        "SELECT * FROM tasks",
        "SELECT * FROM analytics_owner.tasks",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * INTO stolen FROM analytics.tasks",
        "SELECT * FROM analytics.tasks FOR UPDATE",
        "WITH x AS (DELETE FROM tasks RETURNING *) SELECT * FROM x",
        "WITH x AS (SELECT * FROM analytics.tasks) SELECT * FROM x",
        "SELECT pg_sleep(10) FROM analytics.tasks",
        "SELECT set_config('role', 'admin', false) FROM analytics.tasks",
        "SELECT pg_read_file('/etc/passwd') FROM analytics.tasks",
        "SELECT public.count(*) FROM analytics.tasks",
        "SELECT owner::regclass FROM analytics.tasks",
        'SELECT owner COLLATE "C" FROM analytics.tasks',
        "SELECT (SELECT owner FROM public.tasks) FROM analytics.tasks",
        "SELECT * FROM analytics.tasks UNION SELECT * FROM public.tasks",
        "SELECT * FROM analytics.tasks a JOIN analytics.tasks b ON a.id=b.id",
        "SELECT tableoid FROM analytics.tasks",
        "SELECT extra_metadata FROM analytics.tasks",
        "SELECT * FROM analytics.tasks LIMIT -1",
        "SELECT * FROM analytics.tasks OFFSET 999999",
        "SELECT * FROM analytics.tasks TABLESAMPLE SYSTEM (1)",
        "SELECT * FROM analytics.tasks WHERE owner OPERATOR(public.=) 'Ada'",
        "SELECT * FROM analytics.tasks WHERE id IN (SELECT id FROM public.tasks)",
    ],
)
def test_reject_unsafe_sql(sql):
    with pytest.raises(UnsafeQuery):
        validate_sql(sql)


def test_roles_and_limit():
    assert "analytics_masked.tasks" in validate_sql("SELECT owner FROM analytics.tasks")
    assert "analytics_owner.tasks" in validate_sql(
        "SELECT owner FROM analytics.tasks", read_owners=True
    )
    assert validate_sql("SELECT * FROM analytics.tasks LIMIT 9999").endswith("LIMIT 201")
    assert validate_sql("SELECT * FROM analytics.tasks LIMIT 2").endswith("LIMIT 2")
    assert "--" not in validate_sql("SELECT * FROM analytics.tasks -- ignore rules")


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: case["question"])
def test_reference_eval(case):
    conn = fixture_connection()
    try:
        assert [list(row) for row in conn.execute(validate_sql(case["sql"]))] == case["expected"]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "sql,expected",
    [
        (
            "SELECT owner AS email FROM analytics.tasks WHERE has_owner = TRUE LIMIT 1",
            [["[MASKED]"]],
        ),
        ("SELECT MAX(owner) FROM analytics.tasks", [["[MASKED]"]]),
        ("SELECT COUNT(*) FROM analytics.tasks WHERE owner = 'Ada'", [[0]]),
    ],
)
def test_masking_cannot_be_bypassed_by_alias_aggregate_or_filter(sql, expected):
    conn = fixture_connection()
    try:
        assert [list(row) for row in conn.execute(validate_sql(sql))] == expected
    finally:
        conn.close()


@pytest.fixture
def configured(monkeypatch):
    settings = Settings(
        _env_file=None, intelligence_api_key="normal", intelligence_owner_api_key="privileged"
    )
    monkeypatch.setattr("app.api.analytics.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.analytics.get_settings", lambda: settings)
    return settings


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/intelligence")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("key,permission", [("normal", False), ("privileged", True)])
async def test_endpoint_permissions(configured, client, monkeypatch, key, permission):
    generate = AsyncMock(return_value="SELECT COUNT(*) FROM analytics.tasks")
    execute = AsyncMock(
        return_value={
            "columns": ["count"],
            "rows": [[6]],
            "truncated": False,
            "owners_masked": not permission,
        }
    )
    monkeypatch.setattr("app.api.analytics.generate_sql", generate)
    monkeypatch.setattr("app.api.analytics.execute_sql", execute)
    async with client:
        response = await client.post(
            "/api/intelligence/ask",
            json={"question": "How many tasks?"},
            headers={"X-API-Key": key, "X-Permissions": "read_owners"},
        )
    assert response.status_code == 200
    generate.assert_awaited_once_with("How many tasks?", read_owners=permission)
    execute.assert_awaited_once_with(generate.return_value, read_owners=permission)


@pytest.mark.parametrize("key", [None, "wrong"])
async def test_unauthorized_never_calls_model(configured, client, monkeypatch, key):
    generate = AsyncMock()
    monkeypatch.setattr("app.api.analytics.generate_sql", generate)
    async with client:
        response = await client.post(
            "/api/intelligence/ask",
            json={"question": "count tasks"},
            headers={"X-API-Key": key} if key else {},
        )
    assert response.status_code == 401
    generate.assert_not_called()


@pytest.mark.parametrize("normal,owner", [("", ""), ("same", "same")])
async def test_auth_fails_closed(configured, client, normal, owner):
    from pydantic import SecretStr

    configured.intelligence_api_key = SecretStr(normal)
    configured.intelligence_owner_api_key = SecretStr(owner)
    async with client:
        response = await client.post(
            "/api/intelligence/ask", json={"question": "count tasks"}, headers={"X-API-Key": "same"}
        )
    assert response.status_code == 503


async def test_no_database_fallback(configured):
    with pytest.raises(AnalyticsUnavailable):
        await execute_sql("SELECT COUNT(*) FROM analytics.tasks")


async def test_unsafe_model_output_not_executed(configured, client, monkeypatch):
    monkeypatch.setattr(
        "app.api.analytics.generate_sql", AsyncMock(return_value="DROP TABLE tasks")
    )
    async with client:
        response = await client.post(
            "/api/intelligence/ask",
            json={"question": "delete tasks"},
            headers={"X-API-Key": "normal"},
        )
    assert response.status_code == 422
    assert "DROP" not in response.text


@pytest.mark.parametrize("privileged", [False, True])
async def test_executor_readonly_timeout_limit_and_role(configured, monkeypatch, privileged):
    from unittest.mock import MagicMock

    from pydantic import SecretStr

    configured.intelligence_database_url = SecretStr("postgresql://example/db")
    configured.intelligence_owner_database_url = SecretStr("postgresql://example/db")
    role = "aoi_analytics_owner" if privileged else "aoi_analytics_masked"
    identity = {
        "name": role,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolbypassrls": False,
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=MagicMock())
    connection.execute.return_value.mappings.return_value.one.return_value = identity
    result = MagicMock()
    result.keys.return_value = ["id"]
    result.fetchmany.return_value = [[str(i)] for i in range(201)]
    connection.exec_driver_sql = AsyncMock(return_value=result)
    connection.begin.return_value.__aenter__ = AsyncMock()
    connection.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    engine.dispose = AsyncMock()
    monkeypatch.setattr("app.services.analytics.create_async_engine", lambda *a, **k: engine)
    output = await execute_sql("SELECT id FROM analytics.tasks", read_owners=privileged)
    assert output["truncated"] and len(output["rows"]) == 200
    assert output["owners_masked"] is not privileged
    commands = [str(call.args[0]) for call in connection.execute.await_args_list]
    assert commands[:4] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL lock_timeout = '1s'",
        "SET LOCAL search_path = pg_catalog",
    ]
    connection.exec_driver_sql.assert_awaited_once_with(
        validate_sql("SELECT id FROM analytics.tasks", read_owners=privileged)
    )
    engine.dispose.assert_awaited_once()
    # Fail before running model SQL if an application/admin connection is configured.
    identity["name"] = "aoi"
    connection.exec_driver_sql.reset_mock()
    with pytest.raises(AnalyticsUnavailable):
        await execute_sql("SELECT id FROM analytics.tasks", read_owners=privileged)
    connection.exec_driver_sql.assert_not_awaited()
