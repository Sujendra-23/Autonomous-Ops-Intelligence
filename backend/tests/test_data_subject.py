"""Data-subject export and erasure, against real migrations and a nonprivileged runtime role.

Row-level security is forced on the workspace tables, so these tests use the same setup as the
release isolation gate: a throwaway database migrated with alembic, accessed through a
NOSUPERUSER NOBYPASSRLS role, and inspected through a superuser connection that sees every row.
"""

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import psycopg2
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import auth
from app.api import account as account_api
from app.config import base_settings
from app.database import Base, get_session
from app.models import (
    Blocker,
    Decision,
    Project,
    Risk,
    Task,
    TaskActivity,
    Transcript,
    TranscriptChunk,
    WebhookDelivery,
)
from app.security import SecurityMiddleware
from app.services import data_subject
from app.tenancy import workspace_context

CONFIRM = data_subject.CONFIRMATION_PHRASE
SECRET_VALUE = "xoxb-super-secret-connector-value"
WEBHOOK_SECRET_URL = "https://hooks.example.com/services/T000/B000/webhook-credential"


def _admin_url() -> str:
    url = os.environ.get("SAAS_TEST_ADMIN_URL")
    if not url:
        if os.environ.get("REQUIRE_DATABASE_TESTS") == "true":
            pytest.fail("SAAS_TEST_ADMIN_URL is required for the data-subject tests")
        pytest.skip("Set SAAS_TEST_ADMIN_URL to run the data-subject tests")
    return url


@pytest.fixture(scope="session")
def template_database():
    """Migrate once into a template; each test clones it in milliseconds."""
    admin_url = _admin_url()
    suffix = uuid.uuid4().hex[:10]
    template, role = f"aoi_dsr_tpl_{suffix}", f"aoi_dsr_rt_{suffix}"
    parsed = urlsplit(admin_url)
    connection = psycopg2.connect(admin_url)
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute(f'CREATE DATABASE "{template}"')
    target = urlunsplit(parsed._replace(path="/" + template))
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and module, isolated test env
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={
            **os.environ,
            "DATABASE_URL": target.replace("postgresql://", "postgresql+asyncpg://"),
            "ENVIRONMENT": "development",
            "AUTH_MODE": "development",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    cursor.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'test-only' NOSUPERUSER NOBYPASSRLS")
    template_connection = psycopg2.connect(target)
    template_connection.autocommit = True
    inner = template_connection.cursor()
    inner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    inner.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
    )
    template_connection.close()
    yield {"admin_url": admin_url, "template": template, "role": role, "parsed": parsed}
    cursor.execute(f'DROP DATABASE IF EXISTS "{template}" WITH (FORCE)')
    cursor.execute(f'DROP ROLE IF EXISTS "{role}"')
    connection.close()


@pytest_asyncio.fixture
async def env(template_database, monkeypatch):
    for key, value in {
        "AUTH_MODE": "oidc",
        "AUTH_ISSUER": "https://identity.example/",
        "AUTH_AUDIENCE": "api",
        "AUTH_JWKS_URL": "https://identity.example/keys",
        "CONNECTOR_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(key, value)
    base_settings.cache_clear()
    tpl = template_database
    database = "aoi_dsr_" + uuid.uuid4().hex[:10]
    admin = await asyncpg.connect(tpl["admin_url"])
    await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE "{tpl["template"]}"')
    await admin.close()
    superuser = await asyncpg.connect(
        urlunsplit(tpl["parsed"]._replace(path="/" + database))
    )
    runtime = create_async_engine(
        f"postgresql+asyncpg://{tpl['role']}:test-only@"
        f"{tpl['parsed'].hostname}:{tpl['parsed'].port or 5432}/{database}"
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    yield {"db": superuser, "factory": factory, "key": base_settings().connector_encryption_key}
    await runtime.dispose()
    await superuser.close()
    admin = await asyncpg.connect(tpl["admin_url"])
    await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
    await admin.close()
    base_settings.cache_clear()


class World:
    """Three customers. A owns a private workspace and belongs to one shared with B."""

    def __init__(self, env):
        self.db = env["db"]
        self.factory = env["factory"]
        self.a, self.b, self.c, self.d = (uuid.uuid4() for _ in range(4))
        self.wa, self.shared, self.wc = (uuid.uuid4() for _ in range(3))
        self.token_hashes = {name: uuid.uuid4().hex + uuid.uuid4().hex for name in "ABDx"}
        self.cipher = Fernet(env["key"].get_secret_value().encode())

    async def build(self):
        db = self.db
        for account, subject in ((self.a, "subject-a"), (self.b, "subject-b"),
                                 (self.c, "subject-c"), (self.d, "subject-d")):
            await db.execute("INSERT INTO accounts(id,subject) VALUES($1,$2)", account, subject)
        ciphertext = self.cipher.encrypt(
            json.dumps({"slack_bot_token": SECRET_VALUE, "slack_default_channel": "C1",
                        "jira_email": ""}).encode()
        ).decode()
        self.ciphertext = ciphertext
        await db.execute(
            "INSERT INTO workspaces(id,name,connector_ciphertext) VALUES($1,'Private A',$2)",
            self.wa, ciphertext,
        )
        await db.execute("INSERT INTO workspaces(id,name) VALUES($1,'Shared B and A')", self.shared)
        await db.execute("INSERT INTO workspaces(id,name) VALUES($1,'Customer C')", self.wc)
        for account, workspace, role in (
            (self.a, self.wa, "owner"), (self.a, self.shared, "admin"),
            (self.b, self.shared, "owner"), (self.c, self.wc, "owner"),
        ):
            await db.execute(
                "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,$3)",
                account, workspace, role,
            )
        expires = datetime.now(UTC) + timedelta(days=10)
        for name, account, workspace in (
            ("A", self.a, self.wa), ("B", self.b, self.shared),
            ("x", self.a, self.shared),
            # A previously removed member's token is left behind when a membership is removed.
            ("D", self.d, self.wa),
        ):
            await db.execute(
                "INSERT INTO access_tokens(id,account_id,workspace_id,name,token_hash,expires_at)"
                " VALUES($1,$2,$3,$4,$5,$6)",
                uuid.uuid4(), account, workspace, f"token-{name}", self.token_hashes[name], expires,
            )
        for workspace, label in ((self.wa, "wa"), (self.shared, "shared"), (self.wc, "wc")):
            await self.seed(workspace, label)
        return self

    async def seed(self, workspace, label):
        token = workspace_context.set(workspace)
        try:
            async with self.factory() as db:
                project = Project(name=f"project-{label}", slug=f"project-{label}")
                db.add(project)
                await db.flush()
                transcript = Transcript(
                    title=f"title-{label}", content=f"content-{label}", project_id=project.id
                )
                db.add(transcript)
                await db.flush()
                task = Task(title=f"task-{label}", owner=f"owner-{label}", project_id=project.id,
                            transcript_id=transcript.id)
                db.add(task)
                await db.flush()
                db.add_all([
                    TranscriptChunk(transcript_id=transcript.id, index=0, content=f"chunk-{label}",
                                    embedding=[0.25] * 1536),
                    TaskActivity(task_id=task.id, kind=f"activity-{label}"),
                    Decision(summary=f"decision-{label}", project_id=project.id),
                    Risk(title=f"risk-{label}", project_id=project.id),
                    Blocker(summary=f"blocker-{label}", project_id=project.id, task_id=task.id),
                    WebhookDelivery(
                        event_type="task.created", destination=WEBHOOK_SECRET_URL,
                        payload={"label": label}, next_attempt_at=datetime.now(UTC),
                    ),
                ])
                await db.commit()
        finally:
            workspace_context.reset(token)

    async def count(self, table, column=None, value=None):
        if column is None:
            return await self.db.fetchval(f'SELECT count(*) FROM "{table}"')  # noqa: S608
        return await self.db.fetchval(
            f'SELECT count(*) FROM "{table}" WHERE "{column}" = $1', value  # noqa: S608
        )

    async def snapshot(self):
        """Row count of every table, plus per-workspace counts: detects any change at all."""
        tables = await self.db.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1"
        )
        result = {}
        for row in tables:
            name = row["table_name"]
            result[name] = await self.count(name)
        for workspace, label in ((self.wa, "wa"), (self.shared, "shared"), (self.wc, "wc")):
            for table in data_subject.WORKSPACE_DATA_TABLES:
                result[f"{table}@{label}"] = await self.count(table, "workspace_id", workspace)
        return result

    async def erase(self, account=None, workspace=None):
        token = workspace_context.set(workspace or self.wa)
        try:
            async with self.factory() as db:
                try:
                    result = await data_subject.erase_account(db, account or self.a)
                    await db.commit()
                    return result
                except BaseException:
                    await db.rollback()
                    raise
        finally:
            workspace_context.reset(token)


@pytest_asyncio.fixture
async def world(env):
    return await World(env).build()


# --- Export ---------------------------------------------------------------------------------


async def export(world, account=None, workspace=None, **options):
    token = workspace_context.set(workspace or world.wa)
    try:
        async with world.factory() as db:
            return await data_subject.build_export(db, account or world.a, **options)
    finally:
        workspace_context.reset(token)


async def test_export_contains_every_record_of_owned_workspaces(world):
    bundle = await export(world)
    private = next(w for w in bundle["workspaces"] if w["id"] == str(world.wa))
    assert set(private["data"]) == set(data_subject.WORKSPACE_DATA_TABLES)
    for table in data_subject.WORKSPACE_DATA_TABLES:
        expected = await world.count(table, "workspace_id", world.wa)
        assert expected >= 1, f"seed data is missing for {table}"
        assert len(private["data"][table]) == expected, table
    chunk = private["data"]["transcript_chunks"][0]
    assert chunk["content"] == "chunk-wa" and "embedding" not in chunk
    assert private["data"]["tasks"][0]["owner"] == "owner-wa"
    assert bundle["account"]["subject"] == "subject-a"
    assert {m["workspace_id"] for m in bundle["memberships"]} == {
        str(world.wa), str(world.shared)
    }
    assert {t["name"] for t in bundle["access_tokens"]} == {"token-A", "token-x"}


async def test_export_can_include_embeddings(world):
    bundle = await export(world, include_embeddings=True)
    private = next(w for w in bundle["workspaces"] if w["id"] == str(world.wa))
    assert "embedding" in private["data"]["transcript_chunks"][0]


async def test_export_never_contains_secrets_only_their_names(world):
    bundle = await export(world)
    text = json.dumps(bundle)
    for forbidden in (
        SECRET_VALUE, world.ciphertext, WEBHOOK_SECRET_URL, *world.token_hashes.values()
    ):
        assert forbidden not in text
    assert all("token_hash" not in row for row in bundle["access_tokens"])
    private = next(w for w in bundle["workspaces"] if w["id"] == str(world.wa))
    assert "connector_ciphertext" not in private
    assert all("destination" not in row for row in private["data"]["webhook_deliveries"])
    assert private["connector_credentials"] == {
        "stored_encrypted": True,
        "configured_fields": ["slack_bot_token", "slack_default_channel"],
    }
    assert bundle["excluded_secrets"]["access_tokens"] == ["token_hash"]
    assert "destination" in bundle["excluded_secrets"]["webhook_deliveries"]


async def test_export_excludes_other_customers_and_shared_content(world):
    bundle = await export(world)
    text = json.dumps(bundle)
    for absent in ("-wc", "-shared", "subject-b", "subject-c", "subject-d",
                   str(world.b), str(world.c), str(world.d)):
        assert absent not in text
    shared = next(w for w in bundle["workspaces"] if w["id"] == str(world.shared))
    assert shared["data"] is None and shared["your_role"] == "admin"
    assert shared["other_member_count"] == 1
    assert "connector_credentials" not in shared


async def test_export_reaches_every_owned_workspace_under_row_level_security(world):
    second = uuid.uuid4()
    await world.db.execute("INSERT INTO workspaces(id,name) VALUES($1,'Second')", second)
    await world.db.execute(
        "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'owner')",
        world.a, second,
    )
    await world.seed(second, "second")
    bundle = await export(world)
    by_id = {w["id"]: w for w in bundle["workspaces"]}
    assert by_id[str(second)]["data"]["projects"][0]["name"] == "project-second"
    assert by_id[str(world.wa)]["data"]["projects"][0]["name"] == "project-wa"


# --- Erasure --------------------------------------------------------------------------------


async def keyed_columns(db):
    rows = await db.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name IN ('account_id','workspace_id')"
    )
    return [(r["table_name"], r["column_name"]) for r in rows]


async def test_erasure_leaves_nothing_behind(world):
    before = await world.snapshot()
    result = await world.erase()

    # Every table keyed to the account or its private workspace is empty for them.
    for table, column in await keyed_columns(world.db):
        value = world.a if column == "account_id" else world.wa
        assert await world.count(table, column, value) == 0, f"{table}.{column}"
    assert await world.count("accounts", "id", world.a) == 0
    assert await world.count("workspaces", "id", world.wa) == 0
    for token_hash in (world.token_hashes["A"], world.token_hashes["x"],
                       world.token_hashes["D"]):
        assert await world.count("access_tokens", "token_hash", token_hash) == 0
    for table in data_subject.WORKSPACE_DATA_TABLES:
        assert result["deleted"][table] == 1

    # Nobody else's data was touched, including the shared workspace and its other member.
    after = await world.snapshot()
    for key in before:
        if key.endswith("@shared") or key.endswith("@wc"):
            assert after[key] == before[key], key
    assert await world.count("accounts", "id", world.b) == 1
    assert await world.count("accounts", "id", world.c) == 1
    assert await world.count("workspaces", "id", world.shared) == 1
    assert await world.count("memberships", "workspace_id", world.shared) == 1
    assert await world.count("access_tokens", "token_hash", world.token_hashes["B"]) == 1
    assert result["workspaces_retained"] == 1


async def test_erasure_audit_record_holds_no_personal_data(world):
    result = await world.erase()
    rows = await world.db.fetch("SELECT * FROM data_subject_audit")
    assert len(rows) == 1
    row = rows[0]
    assert str(row["id"]) == result["audit_id"]
    assert row["subject_digest"] == data_subject.subject_digest(world.a)
    rendered = json.dumps(dict(row), default=str)
    for personal in (str(world.a), "subject-a", str(world.wa), str(world.shared), "Private A",
                     "title-wa", "content-wa", "project-wa", SECRET_VALUE,
                     world.token_hashes["A"], str(world.b), "subject-b"):
        assert personal not in rendered
    assert json.loads(row["details"])["deleted_rows"]["transcripts"] == 1


async def test_erasure_blocked_when_sole_owner_of_shared_workspace(world):
    await world.db.execute(
        "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'member')",
        world.c, world.wa,
    )
    before = await world.snapshot()
    with pytest.raises(data_subject.ErasureBlocked) as blocked:
        await world.erase()
    assert [w["id"] for w in blocked.value.workspaces] == [str(world.wa)]
    assert await world.snapshot() == before


async def test_co_owner_can_leave_without_deleting_the_workspace(world):
    await world.db.execute(
        "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'owner')",
        world.c, world.wa,
    )
    before = await world.snapshot()
    await world.erase()
    after = await world.snapshot()
    assert await world.count("workspaces", "id", world.wa) == 1
    for table in data_subject.WORKSPACE_DATA_TABLES:
        assert after[f"{table}@wa"] == before[f"{table}@wa"]
    assert await world.count("memberships", "account_id", world.a) == 0
    assert await world.count("memberships", "workspace_id", world.wa) == 1


async def test_legacy_workspace_is_never_erased(world):
    from app.tenancy import LEGACY_WORKSPACE

    await world.seed(LEGACY_WORKSPACE, "legacy")
    await world.db.execute(
        "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'owner')",
        world.a, LEGACY_WORKSPACE,
    )
    legacy_rows = await world.count("transcripts", "workspace_id", LEGACY_WORKSPACE)
    await world.erase()
    assert await world.count("transcripts", "workspace_id", LEGACY_WORKSPACE) == legacy_rows == 1
    assert await world.count("memberships", "account_id", world.a) == 0


async def test_erasing_an_unknown_account_is_an_error(world):
    with pytest.raises(data_subject.AccountNotFound):
        await world.erase(account=uuid.uuid4())


async def test_erasure_rolls_back_completely_when_the_audit_write_fails(world, monkeypatch):
    before = await world.snapshot()

    async def fail(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(data_subject, "write_audit", fail)
    with pytest.raises(RuntimeError):
        await world.erase()
    assert await world.snapshot() == before
    assert await world.count("data_subject_audit") == 0


async def test_erasure_rolls_back_every_workspace_when_a_later_step_fails(world, monkeypatch):
    second = uuid.uuid4()
    await world.db.execute("INSERT INTO workspaces(id,name) VALUES($1,'Second private')", second)
    await world.db.execute(
        "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'owner')",
        world.a, second,
    )
    await world.seed(second, "second")
    before = await world.snapshot()
    real, calls = data_subject._use_workspace, []

    async def flaky(db, workspace_id):
        calls.append(workspace_id)
        if len(calls) == 2:
            raise RuntimeError("connection lost mid-erasure")
        await real(db, workspace_id)

    monkeypatch.setattr(data_subject, "_use_workspace", flaky)
    with pytest.raises(RuntimeError):
        await world.erase()
    assert len(calls) == 2  # the first workspace really had been processed
    assert await world.snapshot() == before
    assert await world.count("data_subject_audit") == 0
    assert await world.count("accounts", "id", world.a) == 1


async def test_runtime_role_cannot_bypass_row_level_security(world):
    """The tests above are only meaningful if the role under test is actually constrained."""
    async with world.factory() as db:
        from sqlalchemy import text

        assert await db.scalar(text("SELECT count(*) FROM transcripts")) == 0
        assert await db.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ) is False


# --- Coverage: new tables must be classified ------------------------------------------------


async def schema_tables(db):
    rows = await db.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    return [r["table_name"] for r in rows]


def test_every_model_table_is_classified():
    assert data_subject.unclassified_tables(Base.metadata.tables) == []


async def test_every_migrated_table_is_classified(world):
    assert data_subject.unclassified_tables(await schema_tables(world.db)) == []


async def test_every_table_with_an_account_or_workspace_column_is_covered(world):
    covered = data_subject.covered_tables()
    uncovered = [(t, c) for t, c in await keyed_columns(world.db) if t not in covered]
    assert uncovered == [], f"Add these to app/services/data_subject.py: {uncovered}"


async def test_the_coverage_check_fails_for_a_new_uncovered_table(world):
    await world.db.execute("CREATE TABLE user_notes(id uuid PRIMARY KEY, account_id uuid)")
    await world.db.execute("CREATE TABLE team_notes(id uuid PRIMARY KEY, workspace_id uuid)")
    assert data_subject.unclassified_tables(await schema_tables(world.db)) == [
        "team_notes", "user_notes"
    ]
    covered = data_subject.covered_tables()
    assert sorted(t for t, _ in await keyed_columns(world.db) if t not in covered) == [
        "team_notes", "user_notes"
    ]


async def test_workspace_data_tables_match_the_schema(world):
    keyed = {t for t, c in await keyed_columns(world.db) if c == "workspace_id"}
    assert keyed - set(data_subject.ACCOUNT_TABLES) == set(data_subject.WORKSPACE_DATA_TABLES)
    forced = await world.db.fetch(
        "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
        "AND relnamespace = 'public'::regnamespace"
    )
    assert {r["relname"] for r in forced} == set(data_subject.WORKSPACE_DATA_TABLES)
    # Children before parents, so deletes never depend on deferred constraints.
    order = {t: i for i, t in enumerate(data_subject.WORKSPACE_DATA_TABLES)}
    pairs = [
        (fk.parent.table.name, fk.column.table.name)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
    ]
    for child, parent in pairs:
        if child in order and parent in order and child != parent:
            assert order[child] < order[parent], f"{child} must be deleted before {parent}"


def test_secret_columns_exist_and_nothing_sensitive_is_unlisted():
    sensitive = ("hash", "ciphertext", "secret", "password", "credential", "destination")
    allowed = {("access_tokens", "name")}
    for table in data_subject.covered_tables():
        columns = Base.metadata.tables[table].columns.keys()
        for column in data_subject.SECRET_COLUMNS.get(table, ()):
            assert column in columns
        for column in columns:
            if any(part in column for part in sensitive) and (table, column) not in allowed:
                assert column in data_subject.SECRET_COLUMNS.get(table, ()), (
                    f"{table}.{column} looks secret; add it to SECRET_COLUMNS"
                )


# --- HTTP layer: authentication, confirmation, roles -----------------------------------------


class FakeRedis:
    async def eval(self, *args):
        return 1

    async def set(self, *args, **kwargs):
        return True


@pytest_asyncio.fixture
async def client(world, monkeypatch):
    import app.security as security

    state = {"role": "owner", "workspace": world.wa, "account": world.a}

    async def authenticate(credential, workspace_header=None):
        return auth.Principal(state["account"], state["workspace"], state["role"], None)

    monkeypatch.setattr(security, "authenticate", AsyncMock(side_effect=authenticate))
    app = FastAPI()
    app.include_router(account_api.router, prefix="/api/account")

    async def session():
        async with world.factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    middleware = SecurityMiddleware(app)
    middleware.redis = FakeRedis()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as http:
        http.state = state
        yield http


BEARER = {"Authorization": "Bearer oidc-session"}


async def test_http_export_returns_a_downloadable_bundle(world, client):
    response = await client.get("/api/account/data-export", headers=BEARER)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["account"]["id"] == str(world.a)
    assert SECRET_VALUE not in response.text


async def test_http_deletion_requires_the_exact_confirmation(world, client):
    before = await world.snapshot()
    for confirmation in ("", "yes", CONFIRM.lower(), CONFIRM + " "):
        response = await client.post(
            "/api/account/data-deletion",
            json={"account_id": str(world.a), "confirmation": confirmation},
            headers=BEARER,
        )
        assert response.status_code == 422
    response = await client.post(
        "/api/account/data-deletion", json={"account_id": str(world.a)}, headers=BEARER
    )
    assert response.status_code == 422
    assert await world.snapshot() == before


async def test_http_deletion_only_for_the_authenticated_account(world, client):
    before = await world.snapshot()
    response = await client.post(
        "/api/account/data-deletion",
        json={"account_id": str(world.b), "confirmation": CONFIRM},
        headers=BEARER,
    )
    assert response.status_code == 403
    assert await world.snapshot() == before


async def test_http_deletion_requires_authentication(world, client):
    response = await client.post(
        "/api/account/data-deletion",
        json={"account_id": str(world.a), "confirmation": CONFIRM},
    )
    assert response.status_code == 401
    assert await world.count("accounts", "id", world.a) == 1


async def test_extension_tokens_cannot_export_or_delete(world, client):
    before = await world.snapshot()
    headers = {"X-API-Key": "aoi_extension-token"}
    assert (await client.get("/api/account/data-export", headers=headers)).status_code == 403
    response = await client.post(
        "/api/account/data-deletion",
        json={"account_id": str(world.a), "confirmation": CONFIRM},
        headers=headers,
    )
    assert response.status_code == 403
    assert await world.snapshot() == before


async def test_http_deletion_succeeds_and_returns_counts(world, client):
    response = await client.post(
        "/api/account/data-deletion",
        json={"account_id": str(world.a), "confirmation": CONFIRM},
        headers=BEARER,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "deleted" and body["deleted"]["accounts"] == 1
    assert await world.count("accounts", "id", world.a) == 0
    assert await world.count("workspaces", "id", world.wa) == 0
    assert await world.count("data_subject_audit") == 1


async def test_http_deletion_reports_blocking_workspaces(world, client):
    await world.db.execute(
        "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'viewer')",
        world.c, world.wa,
    )
    before = await world.snapshot()
    response = await client.post(
        "/api/account/data-deletion",
        json={"account_id": str(world.a), "confirmation": CONFIRM},
        headers=BEARER,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["workspaces"] == [{"id": str(world.wa), "name": "Private A"}]
    assert await world.snapshot() == before


async def test_a_viewer_can_erase_their_own_account_but_not_modify_workspaces(world, client):
    """Read-only roles keep their rights over their own data, and nothing else."""
    client.state.update(role="viewer", workspace=world.shared)
    blocked = await client.post(
        "/api/account/tokens", json={"name": "x"}, headers=BEARER
    )
    assert blocked.status_code == 403
    assert (await client.get("/api/account/data-export", headers=BEARER)).status_code == 200
    response = await client.post(
        "/api/account/data-deletion",
        json={"account_id": str(world.a), "confirmation": CONFIRM},
        headers=BEARER,
    )
    assert response.status_code == 200, response.text
    assert await world.count("accounts", "id", world.a) == 0
    assert await world.count("workspaces", "id", world.shared) == 1
