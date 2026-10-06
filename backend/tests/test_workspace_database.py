"""Exercise real migrations and forced RLS using a nonprivileged runtime role."""

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import base_settings
from app.models.project import Project
from app.models.transcript import Transcript
from app.tenancy import workspace_context


@pytest.mark.asyncio
async def test_migrations_and_customer_isolation(monkeypatch):
    admin_url = os.environ.get("SAAS_TEST_ADMIN_URL")
    if not admin_url:
        if os.environ.get("REQUIRE_DATABASE_TESTS") == "true":
            pytest.fail("SAAS_TEST_ADMIN_URL is required for the release isolation gate")
        pytest.skip("Set SAAS_TEST_ADMIN_URL to run the migration/RLS release gate")
    suffix = uuid.uuid4().hex[:12]
    database, role = "aoi_test_" + suffix, "aoi_runtime_" + suffix
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(admin_url)
    target = urlunsplit(parsed._replace(path="/" + database))
    runtime_url = (
        f"postgresql+asyncpg://{role}:test-only-password@"
        f"{parsed.hostname}:{parsed.port or 5432}/{database}"
    )
    runtime = None
    owner = None
    try:
        env = {
            **os.environ,
            "DATABASE_URL": target.replace("postgresql://", "postgresql+asyncpg://"),
            "ENVIRONMENT": "development",
            "AUTH_MODE": "development",
        }
        result = subprocess.run(  # noqa: S603 -- fixed interpreter, module and isolated test env
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        await admin.execute(
            f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'test-only-password' NOSUPERUSER NOBYPASSRLS"
        )
        owner = await asyncpg.connect(target)
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
        )
        a, b, account_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await owner.execute(
            "INSERT INTO workspaces(id,name) VALUES($1,'Customer A'),($2,'Customer B')", a, b
        )
        await owner.execute("INSERT INTO accounts(id,subject) VALUES($1,'test-user')", account_id)
        await owner.execute(
            "INSERT INTO memberships(account_id,workspace_id,role) VALUES($1,$2,'member')",
            account_id,
            a,
        )
        for key, value in {
            "AUTH_MODE": "oidc",
            "AUTH_ISSUER": "https://identity.example/",
            "AUTH_AUDIENCE": "api",
            "AUTH_JWKS_URL": "https://identity.example/keys",
            "CONNECTOR_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        }.items():
            monkeypatch.setenv(key, value)
        base_settings.cache_clear()
        runtime = create_async_engine(runtime_url)
        factory = async_sessionmaker(runtime, expire_on_commit=False)
        ids = []
        for workspace in (a, b):
            token = workspace_context.set(workspace)
            try:
                async with factory() as db:
                    project = Project(name="Same project", slug="same-project")
                    db.add(project)
                    await db.flush()
                    transcript = Transcript(
                        title="Private meeting",
                        content="Customer confidential content",
                        project_id=project.id,
                    )
                    db.add(transcript)
                    await db.commit()
                    ids.append((project.id, transcript.id))
            finally:
                workspace_context.reset(token)
        token = workspace_context.set(a)
        try:
            async with factory() as db:
                assert len((await db.scalars(select(Project))).all()) == 1
                assert await db.get(Transcript, ids[1][1]) is None
                assert await db.scalar(text("SELECT count(*) FROM transcripts")) == 1
                result = await db.execute(
                    update(Project).where(Project.id == ids[1][0]).values(name="Hacked")
                )
                assert result.rowcount == 0
                await db.commit()
                db.add(
                    Transcript(
                        title="Cross-tenant reference", content="invalid", project_id=ids[1][0]
                    )
                )
                with pytest.raises(DBAPIError):
                    await db.commit()
                await db.rollback()
                with pytest.raises(DBAPIError):
                    await db.execute(
                        text(
                            "INSERT INTO projects(id,workspace_id,name,slug,status) "
                            "VALUES(:id,:workspace,'Bad','bad','active')"
                        ),
                        {"id": uuid.uuid4(), "workspace": b},
                    )
                await db.rollback()
            # Actual token authentication uses membership on every call, including revocation.
            from app import auth

            monkeypatch.setattr(auth, "SessionLocal", factory)
            token_value = "aoi_test-token-with-entropy"
            token_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO access_tokens(id,account_id,workspace_id,name,token_hash,expires_at) "
                "VALUES($1,$2,$3,'Extension',$4,$5)",
                token_id,
                account_id,
                a,
                hashlib.sha256(token_value.encode()).hexdigest(),
                datetime.now(UTC) + timedelta(days=1),
            )
            assert (await auth.authenticate(token_value)).workspace_id == a
            with pytest.raises(HTTPException) as error:
                await auth.authenticate(token_value, str(b))
            assert error.value.status_code == 403
            await owner.execute("UPDATE access_tokens SET revoked_at=now() WHERE id=$1", token_id)
            with pytest.raises(HTTPException) as error:
                await auth.authenticate(token_value)
            assert error.value.status_code == 401
        finally:
            workspace_context.reset(token)
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM transcripts")) == 0
    finally:
        base_settings.cache_clear()
        if runtime:
            await runtime.dispose()
        if owner:
            await owner.close()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
