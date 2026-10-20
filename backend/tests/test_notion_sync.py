"""Notion reconciliation, mapping recovery and connector permission regressions."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet

from app.auth import Principal
from app.config import Settings, base_settings
from app.integrations import notion, notion_sync
from app.integrations.notion_permissions import NotionAccessError, require_notion_write
from app.integrations.task_sync import mark_sync_pending
from app.models.project import Project
from app.models.task import Task, TaskActivity
from app.tenancy import (
    connector_service_context,
    principal_context,
    settings_context,
    workspace_context,
)

ROOT, PAGE, BLOCK = (str(uuid.uuid4()) for _ in range(3))


@pytest.fixture
def config():
    config = Settings(
        _env_file=None,
        notion_api_key="notion-secret",
        notion_parent_page_id=ROOT,
        notion_sync_enabled=True,
    )
    token = settings_context.set(config)
    yield config
    settings_context.reset(token)


def records(**values):
    project = Project(id=uuid.uuid4(), name="Project", slug="project", notion_page_id=PAGE)
    row = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        title="Ship fix",
        status="open",
        notion_block_id=BLOCK,
        notion_sync_pending=False,
    )
    for key, value in values.items():
        setattr(row, key, value)
    return project, row


def provider(monkeypatch, *, checked=False, moved=False, status=200, children=None):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer notion-secret"
        if status != 200:
            return httpx.Response(status, json={"message": "private-provider-details"})
        if request.url.path == f"/v1/pages/{PAGE}":
            return httpx.Response(200, json={"parent": {"page_id": ROOT}})
        if request.url.path.endswith("/children"):
            if request.method == "GET":
                return httpx.Response(200, json={"results": children or [], "has_more": False})
            return httpx.Response(200, json={"results": [{"id": BLOCK}]})
        return httpx.Response(
            200,
            json={
                "type": "to_do",
                "to_do": {"checked": checked},
                "parent": {"page_id": str(uuid.uuid4()) if moved else PAGE},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handle))
    )
    return calls


def session_for(project, row):
    return MagicMock(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])),
        get=AsyncMock(return_value=project),
        commit=AsyncMock(),
    )


@pytest.mark.parametrize(
    "before,checked,after",
    [
        ("open", True, "done"),
        ("done", False, "open"),
        ("blocked", False, "blocked"),
        ("in_progress", False, "in_progress"),
        ("cancelled", False, "cancelled"),
        ("done", True, "done"),
    ],
)
async def test_pull_status(config, monkeypatch, before, checked, after):
    project, row = records(status=before)
    calls = provider(monkeypatch, checked=checked)
    db = session_for(project, row)
    assert await notion_sync.sync_notion_statuses(db) == int(before != after)
    assert row.status == after
    assert not row.notion_sync_pending
    assert row.notion_sync_error is None
    assert all(r.method == "GET" for r in calls)
    activities = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], TaskActivity)]
    assert len(activities) == int(before != after)
    if activities:
        assert activities[0].actor == "notion"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "local,checked,expected", [("done", False, True), ("blocked", True, False)]
)
async def test_pending_local_edit_wins(config, monkeypatch, local, checked, expected):
    project, row = records(status=local, notion_sync_pending=True)
    calls = provider(monkeypatch, checked=checked)
    assert await notion_sync.sync_notion_statuses(session_for(project, row)) == 0
    assert row.status == local
    assert not row.notion_sync_pending
    assert json.loads(calls[-1].content) == {"to_do": {"checked": expected}}


@pytest.mark.parametrize("status,moved", [(403, False), (404, False), (429, False), (200, True)])
async def test_failed_access_preserves_pending_and_status(config, monkeypatch, status, moved):
    project, row = records(notion_sync_pending=True)
    calls = provider(monkeypatch, status=status, moved=moved)
    assert await notion_sync.sync_notion_statuses(session_for(project, row)) == 0
    assert row.status == "open" and row.notion_sync_pending
    assert row.notion_sync_error and "private-provider-details" not in row.notion_sync_error
    assert all(r.method == "GET" for r in calls)


async def test_disabled_sync_does_not_query(config):
    config.notion_sync_enabled = False
    db = session_for(*records())
    assert await notion_sync.sync_notion_statuses(db) == 0
    db.scalars.assert_not_awaited()


async def test_notion_change_queues_primary_issue_update(config, monkeypatch):
    config.task_sync_enabled = True
    project, row = records(linear_issue_id="issue")
    provider(monkeypatch, checked=True)
    await notion_sync.sync_notion_statuses(session_for(project, row))
    assert row.sync_pending and row.sync_checked_at is None


def test_local_change_marks_both_mirrors_pending(config):
    config.task_sync_enabled = True
    _, row = records(linear_issue_id="issue")
    mark_sync_pending(row)
    assert row.sync_pending and row.notion_sync_pending


async def test_mirror_persists_block_mapping(config, monkeypatch):
    project, row = records(notion_block_id=None)
    calls = provider(monkeypatch)
    result = await notion.NotionAdapter().create_task(row, project)
    assert result.success and row.notion_block_id == BLOCK
    assert row.notion_sync_pending
    payload = json.loads(calls[-1].content)
    assert payload["children"][0]["type"] == "to_do"
    assert str(row.id) in payload["children"][0]["to_do"]["rich_text"][0]["text"]["content"]


async def test_mirror_recovers_block_after_lost_commit(config, monkeypatch):
    project, row = records(notion_block_id=None)
    calls = provider(
        monkeypatch,
        children=[
            {
                "id": BLOCK,
                "type": "to_do",
                "to_do": {"rich_text": [{"plain_text": f"Ship fix [AOI:{row.id}]"}]},
            }
        ],
    )
    assert (await notion.NotionAdapter().create_task(row, project)).success
    assert row.notion_block_id == BLOCK
    assert all(r.method == "GET" for r in calls)


@pytest.fixture
def oidc(monkeypatch):
    for key, value in {
        "AUTH_MODE": "oidc",
        "AUTH_ISSUER": "https://identity.example/",
        "AUTH_AUDIENCE": "api",
        "AUTH_JWKS_URL": "https://identity.example/keys",
        "CONNECTOR_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(key, value)
    base_settings.cache_clear()
    yield
    base_settings.cache_clear()


@pytest.mark.parametrize(
    "role,allowed", [("owner", True), ("admin", True), ("member", True), ("viewer", False)]
)
def test_connector_checks_workspace_role_at_write_time(oidc, config, role, allowed):
    workspace = uuid.uuid4()
    p = Principal(uuid.uuid4(), workspace, role, None)
    tokens = [
        (workspace_context, workspace_context.set(workspace)),
        (principal_context, principal_context.set(p)),
    ]
    try:
        if allowed:
            require_notion_write(Task(workspace_id=workspace))
        else:
            with pytest.raises(NotionAccessError):
                require_notion_write(Task(workspace_id=workspace))
        with pytest.raises(NotionAccessError):
            require_notion_write(Task(workspace_id=uuid.uuid4()))
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def test_background_writes_require_explicit_scoped_worker(oidc, config):
    token = workspace_context.set(uuid.uuid4())
    try:
        with pytest.raises(NotionAccessError):
            require_notion_write()
        service_token = connector_service_context.set(True)
        try:
            require_notion_write()
        finally:
            connector_service_context.reset(service_token)
    finally:
        workspace_context.reset(token)


async def test_reconcile_persists_status_and_does_not_duplicate_activity(
    config, monkeypatch, db_session
):
    project = Project(name="Synced project", slug="synced-project", notion_page_id=PAGE)
    db_session.add(project)
    await db_session.flush()
    row = Task(project_id=project.id, title="Ship", notion_block_id=BLOCK)
    db_session.add(row)
    await db_session.commit()
    provider(monkeypatch, checked=True)
    assert await notion_sync.sync_notion_statuses(db_session, page_id=PAGE) == 1
    assert await notion_sync.sync_notion_statuses(db_session, page_id=PAGE) == 0
    await db_session.refresh(row)
    assert row.status == "done"
    from sqlalchemy import select

    activities = (
        await db_session.scalars(select(TaskActivity).where(TaskActivity.task_id == row.id))
    ).all()
    assert len(activities) == 1


@pytest.mark.parametrize(
    "role,expected", [("owner", 200), ("admin", 200), ("member", 200), ("viewer", 403)]
)
def test_reconcile_route_uses_authenticated_workspace_role(
    oidc, config, monkeypatch, role, expected
):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import app.security as security
    from app.api.integrations import router
    from app.database import get_session

    workspace = uuid.uuid4()
    cipher = Fernet(base_settings().connector_encryption_key.get_secret_value().encode())
    encrypted = cipher.encrypt(
        json.dumps(
            {
                "notion_api_key": "notion-secret",
                "notion_parent_page_id": ROOT,
                "notion_sync_enabled": True,
            }
        ).encode()
    ).decode()
    p = Principal(uuid.uuid4(), workspace, role, encrypted)
    monkeypatch.setattr(security, "authenticate", AsyncMock(return_value=p))
    db = MagicMock(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])), commit=AsyncMock()
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/integrations")

    async def session():
        assert workspace_context.get() == workspace
        yield db

    app.dependency_overrides[get_session] = session
    middleware = security.SecurityMiddleware(app)
    middleware.redis = MagicMock(eval=AsyncMock(return_value=1))
    with TestClient(middleware) as client:
        response = client.post(
            "/api/integrations/notion/reconcile",
            json={"page_id": PAGE},
            headers={"Authorization": "Bearer verified"},
        )
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {"scheduled": 0}
        db.commit.assert_awaited_once()
    else:
        db.scalars.assert_not_awaited()


async def test_reconcile_schedules_only_matching_workspace(config, db_session, oidc):
    from app.api.integrations import NotionReconcileRequest, reconcile_notion
    from app.models.account import Workspace

    workspace_a, workspace_b = uuid.uuid4(), uuid.uuid4()
    # Fixtures create a plain schema; use the scoped ORM to exercise filtering here.
    service_token = connector_service_context.set(True)
    try:
        rows = []
        for workspace in (workspace_a, workspace_b):
            token = workspace_context.set(workspace)
            try:
                db_session.add(Workspace(id=workspace, name="Workspace"))
                project = Project(
                    workspace_id=workspace, name="Project", slug="project", notion_page_id=PAGE
                )
                db_session.add(project)
                await db_session.flush()
                row = Task(
                    workspace_id=workspace,
                    project_id=project.id,
                    title="Task",
                    notion_block_id=str(uuid.uuid4()),
                    notion_sync_checked_at=datetime.now(UTC),
                )
                db_session.add(row)
                await db_session.commit()
                rows.append(row)
            finally:
                workspace_context.reset(token)
        token = workspace_context.set(workspace_a)
        try:
            assert await reconcile_notion(NotionReconcileRequest(page_id=PAGE), db_session) == {
                "scheduled": 1
            }
            assert rows[0].notion_sync_checked_at is None
            assert rows[1].notion_sync_checked_at is not None
        finally:
            workspace_context.reset(token)
    finally:
        connector_service_context.reset(service_token)
