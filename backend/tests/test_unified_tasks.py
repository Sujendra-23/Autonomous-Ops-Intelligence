"""Standalone tracking and inbound connectors, without live provider credentials."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.api import tasks as routes
from app.config import Settings
from app.integrations import oauth, task_import, task_sync
from app.models.task import Task, TaskActivity, TaskImportState
from app.tenancy import workspace_context


@pytest.fixture
def settings(monkeypatch):
    value = Settings(_env_file=None)
    for module in (task_import, task_sync):
        monkeypatch.setattr(module, "get_settings", lambda: value)
    monkeypatch.setattr(oauth, "access_token", AsyncMock(return_value=None))
    return value


def configure_jira(settings):
    settings.jira_base_url = "https://example.atlassian.net"
    settings.jira_email = "member@example.com"
    settings.jira_api_token = SecretStr("private-test-token")
    settings.jira_project_key = "OPS"


def jira_issue(key="OPS-1", issue_id="100", **overrides):
    fields = {
        "summary": "Review the security checklist",
        "description": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Use the latest checklist."}],
                }
            ],
        },
        "assignee": {"displayName": "Alex"},
        "duedate": "2026-12-01",
        "priority": {"name": "Highest"},
        "status": {"id": "1", "statusCategory": {"key": "new"}},
        "project": {"key": "OPS"},
        "updated": "2026-11-10T15:00:00Z",
        **overrides,
    }
    return {"id": issue_id, "key": key, "fields": fields}


def local_task(**kwargs):
    now = datetime.now(UTC)
    return Task(
        id=uuid.uuid4(),
        title="A native task",
        status="open",
        priority="medium",
        source="local",
        sync_pending=False,
        created_at=now,
        last_status_change_at=now,
        **kwargs,
    )


async def test_native_create_without_connectors_or_llm(settings):
    db = MagicMock(flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())

    async def flush():
        row = db.add.call_args_list[0].args[0]
        row.created_at = datetime.now(UTC)

    db.flush.side_effect = flush
    result = await routes.create_task(
        routes.TaskCreate(title="  Plan the next release  ", owner="Alex"), db
    )
    assert result.source == "local" and result.title == "Plan the next release"
    assert result.transcript_id is None and result.jira_issue_url is None
    assert not result.sync_pending
    activity = db.add.call_args_list[1].args[0]
    assert isinstance(activity, TaskActivity) and activity.actor == "user"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("title", ["", "   ", "\n\t"])
def test_task_titles_cannot_be_blank(title):
    with pytest.raises(ValidationError):
        routes.TaskCreate(title=title)
    with pytest.raises(ValidationError):
        routes.TaskUpdate(title=title)


async def test_native_edit_can_clear_owner_due_date_and_description(settings):
    row = local_task(owner="Alex", description="Old context", due_date=datetime.now(UTC))
    db = MagicMock(get=AsyncMock(return_value=row), commit=AsyncMock(), refresh=AsyncMock())
    result = await routes.update_task(
        row.id, routes.TaskUpdate(owner=None, due_date=None, description=None, title="Updated"), db
    )
    assert result.title == "Updated"
    assert result.owner is None and result.due_date is None and result.description is None
    assert not result.sync_pending


async def test_source_search_and_pagination_are_applied(settings):
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = MagicMock(scalar=AsyncMock(return_value=0), execute=AsyncMock(return_value=result))
    await routes.list_tasks(source="jira", q="100%_done", limit=50, offset=50, session=db)
    statement = db.execute.call_args.args[0]
    sql = str(statement)
    assert "tasks.source =" in sql and "lower(tasks.title) LIKE" in sql
    assert statement.compile().params["source_1"] == "jira"
    assert "100/%/_done" in statement.compile().params.values()


async def test_jira_uses_enhanced_search_and_next_page_token(settings):
    configure_jira(settings)
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/rest/api/3/search/jql"
        assert payload["jql"].startswith('project = "OPS"')
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"issues": [jira_issue()], "nextPageToken": "next-page"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = task_import.ImportClient(client)
        rows, cursor = await adapter.page("jira", None)
        assert cursor == "next-page"
        await adapter.page("jira", "previous-page")
    assert requests[1]["nextPageToken"] == "previous-page"
    task = rows[0]
    assert task.title == "Review the security checklist" and task.owner == "Alex"
    assert task.description == "Use the latest checklist."
    assert task.priority == "urgent" and task.status == "open"
    assert task.due_date.tzinfo is not None
    assert task.url == "https://example.atlassian.net/browse/OPS-1"


async def test_jira_rejects_another_project(settings):
    configure_jira(settings)
    issue = jira_issue(project={"key": "OTHER"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"issues": [issue], "isLast": True})
        )
    ) as client:
        with pytest.raises(task_sync.SyncError, match="outside the selected project"):
            await task_import.ImportClient(client).page("jira", None)


async def test_linear_reads_selected_team_and_paginates(settings):
    settings.linear_api_key = SecretStr("private-test-token")
    settings.linear_team_id = "team-1"

    def respond(request):
        body = json.loads(request.content)
        assert body["variables"] == {"team": "team-1", "after": "previous", "first": 100}
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-1",
                                "identifier": "ENG-1",
                                "title": "Resolve the blocker",
                                "description": "A real issue",
                                "priority": 2,
                                "dueDate": None,
                                "assignee": None,
                                "team": {"id": "team-1"},
                                "state": {"id": "triage", "type": "triage"},
                                "url": "https://linear.app/company/issue/ENG-1",
                            }
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        rows, cursor = await task_import.ImportClient(client).page("linear", "previous")
    assert cursor == "next" and rows[0].priority == "high"
    assert rows[0].status == "open" and rows[0].owner is None


async def test_repeated_pagination_cursor_is_an_error(settings):
    configure_jira(settings)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"issues": [], "nextPageToken": "same"})
        )
    ) as client:
        with pytest.raises(task_sync.SyncError, match="repeated a page"):
            await task_import.ImportClient(client).page("jira", "same")


async def test_upsert_reuses_mirror_preserves_evidence_and_pending_status(settings):
    configure_jira(settings)
    async with httpx.AsyncClient() as client:
        remote = task_import.ImportClient(client).normalize("jira", jira_issue())
    row = local_task(
        jira_issue_key="OPS-1",
        jira_issue_url=remote.url,
        source_quote="I will review the checklist.",
    )
    row.source = "meeting"
    row.status, row.sync_pending = "done", True
    db = MagicMock(scalar=AsyncMock(return_value=row))
    outcome = await task_import.upsert_task(db, "jira", remote, settings)
    assert outcome == "updated" and row.status == "done" and row.sync_pending
    assert row.source == "meeting" and row.source_quote == "I will review the checklist."
    assert row.owner == "Alex" and row.title == remote.title
    first_activity = db.add.call_args.args[0]
    assert first_activity.kind == "connector_updated"
    assert await task_import.upsert_task(db, "jira", remote, settings) == "unchanged"
    assert db.add.call_count == 1


async def test_import_identity_is_workspace_and_site_scoped(settings):
    configure_jira(settings)
    async with httpx.AsyncClient() as client:
        remote = task_import.ImportClient(client).normalize("jira", jira_issue())
    workspace = uuid.uuid4()
    token = workspace_context.set(workspace)
    try:
        db = MagicMock(scalar=AsyncMock(return_value=None))
        await task_import.upsert_task(db, "jira", remote, settings)
        task = db.add.call_args_list[0].args[0]
        assert task.workspace_id == workspace and task.source == "jira"
        assert task.external_id == "100" and task.external_scope
        assert workspace in db.scalar.call_args.args[0].compile().params.values()
        original = task.external_scope
        settings.jira_base_url = "https://other.atlassian.net"
        assert original != task_import.identity_scope("jira", settings)
    finally:
        workspace_context.reset(token)


async def test_no_connector_means_no_import_or_network(settings):
    db = MagicMock(scalar=AsyncMock())
    assert await task_import.import_connected_tasks(db) == []
    db.scalar.assert_not_awaited()


@asynccontextmanager
async def savepoint():
    yield


async def test_import_resumes_cursor_and_completes_pass(settings, monkeypatch):
    configure_jira(settings)
    state = TaskImportState(
        provider="jira", scope=task_import.destination_scope("jira", settings), cursor="resume-here"
    )
    db = MagicMock(
        scalar=AsyncMock(side_effect=[True, state]),
        commit=AsyncMock(),
        flush=AsyncMock(),
        begin_nested=savepoint,
    )
    page = AsyncMock(side_effect=[([object()], "page-3"), ([object()], None)])
    monkeypatch.setattr(task_import.ImportClient, "page", page)
    upsert = AsyncMock(return_value="created")
    monkeypatch.setattr(task_import, "upsert_task", upsert)
    results = await task_import.import_connected_tasks(db, force=True)
    assert page.call_args_list[0].args == ("jira", "resume-here")
    assert page.call_args_list[1].args == ("jira", "page-3")
    assert state.cursor is None and state.last_synced_at is not None and state.error is None
    assert results[0]["created"] == 2 and not results[0]["has_more"]
    db.commit.assert_awaited_once()


async def test_worker_throttles_imports_and_reconnect_resets_cursor(settings, monkeypatch):
    configure_jira(settings)
    state = TaskImportState(
        provider="jira",
        scope=task_import.destination_scope("jira", settings),
        cursor="old-page",
        last_attempt_at=datetime.now(UTC),
    )
    db = MagicMock(
        scalar=AsyncMock(side_effect=[True, state]),
        commit=AsyncMock(),
        flush=AsyncMock(),
        begin_nested=savepoint,
    )
    page = AsyncMock(return_value=([], None))
    monkeypatch.setattr(task_import.ImportClient, "page", page)
    assert await task_import.import_connected_tasks(db) == []
    page.assert_not_awaited()
    settings.jira_project_key = "NEW"
    db.scalar.side_effect = [True, state]
    await task_import.import_connected_tasks(db)
    page.assert_awaited_once_with("jira", None)


async def test_busy_worker_does_not_start_duplicate_import(settings, monkeypatch):
    configure_jira(settings)
    db = MagicMock(scalar=AsyncMock(return_value=False), commit=AsyncMock())
    page = AsyncMock()
    monkeypatch.setattr(task_import.ImportClient, "page", page)
    result = await task_import.import_connected_tasks(db, force=True)
    assert result[0]["busy"]
    page.assert_not_awaited()


async def test_provider_failure_keeps_native_tasks_and_uses_safe_error(settings, monkeypatch):
    configure_jira(settings)
    state = TaskImportState(provider="jira", scope=task_import.destination_scope("jira", settings))
    db = MagicMock(
        scalar=AsyncMock(side_effect=[True, state]),
        commit=AsyncMock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        begin_nested=savepoint,
    )
    page = AsyncMock(side_effect=ValueError("private-provider-response"))
    monkeypatch.setattr(task_import.ImportClient, "page", page)
    result = await task_import.import_connected_tasks(db, force=True)
    assert result[0]["created"] == 0 and "private" not in result[0]["error"]
    assert "read access" in result[0]["error"]
    db.delete.assert_not_called()


async def test_native_and_imported_tasks_roundtrip_and_deduplicate(db_session, settings):
    configure_jira(settings)
    original = local_task()
    db_session.add(original)
    await db_session.commit()
    async with httpx.AsyncClient() as client:
        remote = task_import.ImportClient(client).normalize("jira", jira_issue())
    assert await task_import.upsert_task(db_session, "jira", remote, settings) == "created"
    await db_session.commit()
    assert await task_import.upsert_task(db_session, "jira", remote, settings) == "unchanged"
    await db_session.commit()
    rows = (await db_session.scalars(select(Task))).all()
    assert len(rows) == 2 and {row.source for row in rows} == {"local", "jira"}
    remote.title = "A changed Jira title"
    assert await task_import.upsert_task(db_session, "jira", remote, settings) == "updated"
    await db_session.commit()
    assert await db_session.get(Task, original.id) is not None


async def test_task_create_and_sync_require_api_key(settings, monkeypatch):
    from app.api import deps
    from app.main import app

    settings.ingest_api_key = SecretStr("expected-key")
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/api/tasks", json={"title": "Unauthorized"})).status_code == 401
        assert (await client.post("/api/tasks/sync")).status_code == 401
