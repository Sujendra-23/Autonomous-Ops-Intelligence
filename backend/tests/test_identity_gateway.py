"""Gateway signature, live session, company binding and cookie boundaries."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.testclient import TestClient

from app import auth, identity_gateway
from app.api import gateway_auth
from app.config import Settings, base_settings


@pytest.fixture
def gateway(monkeypatch):
    for key, value in {
        "AUTH_MODE": "oidc",
        "AUTH_PROVIDER": "gateway",
        "AUTH_ISSUER": "https://gateway.local",
        "AUTH_AUDIENCE": "identity-gateway",
        "AUTH_JWKS_URL": "http://gateway:8080/.well-known/jwks.json",
        "IDENTITY_GATEWAY_URL": "http://gateway:8080",
        "CONNECTOR_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ENVIRONMENT": "development",
    }.items():
        monkeypatch.setenv(key, value)
    base_settings.cache_clear()
    key = Ed25519PrivateKey.generate()
    public = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(key.public_key()))
    public.update(kid="gateway-test", use="sig")
    monkeypatch.setattr(auth, "_jwks", {"gateway-test": public})
    monkeypatch.setattr(auth, "_jwks_until", float("inf"))
    tenant, user = uuid.uuid4(), uuid.uuid4()

    def issue(**updates):
        now = datetime.now(UTC)
        claims = {
            "iss": "https://gateway.local",
            "aud": "identity-gateway",
            "sub": str(user),
            "tid": str(tenant),
            "tsl": "acme",
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "roles": ["owner"],
        }
        claims.update(updates)
        return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "gateway-test"})

    check = AsyncMock(return_value={"user_id": str(user), "tenant": "acme", "roles": ["owner"]})
    monkeypatch.setattr(identity_gateway, "gateway_request", check)
    yield issue, check, tenant, user
    base_settings.cache_clear()


@pytest.mark.asyncio
async def test_gateway_verifies_company_and_live_session_on_each_call(gateway):
    issue, check, tenant, user = gateway
    token = issue()
    identity = await identity_gateway.verify_gateway_identity(token)
    assert identity.tenant_id == tenant and identity.subject == str(user)
    assert identity.role == "owner"
    await identity_gateway.verify_gateway_identity(token)
    assert check.await_count == 2
    check.assert_awaited_with("/v1/auth/me", tenant="acme", token=token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"iss": "https://attacker.example"},
        {"aud": "wrong"},
        {"exp": 1},
        {"tid": "not-a-uuid"},
        {"tsl": "other\r\nX-Fake: yes"},
        {"sub": "not-a-user"},
    ],
)
async def test_bad_gateway_claims_never_reach_live_identity_service(gateway, updates):
    issue, check, _, _ = gateway
    with pytest.raises(HTTPException) as error:
        await identity_gateway.verify_gateway_identity(issue(**updates))
    assert error.value.status_code == 401
    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_does_not_accept_unsigned_or_symmetric_tokens(gateway):
    _, check, _, _ = gateway
    with pytest.raises(HTTPException):
        await identity_gateway.verify_gateway_identity(
            jwt.encode(
                {"tid": str(uuid.uuid4())},
                "x" * 32,
                algorithm="HS256",
                headers={"kid": "gateway-test"},
            )
        )
    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_member_is_read_only_and_unknown_roles_fail_closed(gateway):
    issue, check, _, user = gateway
    check.return_value = {"user_id": str(user), "tenant": "acme", "roles": ["member"]}
    assert (await identity_gateway.verify_gateway_identity(issue())).role == "viewer"
    check.return_value["roles"] = ["unrecognized-admin"]
    with pytest.raises(HTTPException) as error:
        await identity_gateway.verify_gateway_identity(issue())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_gateway_revocation_and_identity_mismatch_are_not_cached(gateway):
    issue, check, _, _ = gateway
    token = issue()
    await identity_gateway.verify_gateway_identity(token)
    check.side_effect = HTTPException(401, "revoked")
    with pytest.raises(HTTPException):
        await identity_gateway.verify_gateway_identity(token)
    check.side_effect = None
    check.return_value = {"user_id": str(uuid.uuid4()), "tenant": "acme", "roles": ["owner"]}
    with pytest.raises(HTTPException):
        await identity_gateway.verify_gateway_identity(token)


@pytest.mark.asyncio
async def test_opaque_tokens_cannot_bypass_gateway_revocation(gateway):
    with pytest.raises(HTTPException) as error:
        await auth.authenticate("aoi_old-workspace-token")
    assert error.value.status_code == 401


def test_gateway_configuration_keeps_tenant_security_enabled():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_provider="gateway", auth_mode="development")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            auth_provider="gateway",
            auth_mode="oidc",
            environment="production",
            auth_issuer="https://gateway.local",
            auth_audience="api",
            auth_jwks_url="http://gateway/keys",
            identity_gateway_url="http://gateway",
            connector_encryption_key=Fernet.generate_key().decode(),
        )


def test_login_keeps_refresh_token_in_scoped_httponly_cookie(gateway, monkeypatch):
    issue, _, _, _ = gateway
    pair = {"access_token": issue(), "refresh_token": "opaque-refresh-sentinel"}
    monkeypatch.setattr(gateway_auth, "gateway_request", AsyncMock(return_value=pair))
    app = FastAPI()
    app.include_router(gateway_auth.router, prefix="/auth/gateway")
    with TestClient(app) as client:
        response = client.post(
            "/auth/gateway/login",
            json={
                "company_slug": "acme",
                "email": "owner@acme.test",
                "password": "not-logged-password",
            },
        )
        assert response.status_code == 200
        assert "refresh_token" not in response.json()
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Path=/auth/gateway" in cookie and "SameSite=lax" in cookie
        assert "aoi_gateway_refresh_acme=" in cookie
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.post(
                "/auth/gateway/session", json={"company_slug": "another-company"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/auth/gateway/login",
                headers={"Origin": "https://evil.example"},
                json={
                    "company_slug": "acme",
                    "email": "owner@acme.test",
                    "password": "not-logged-password",
                },
            ).status_code
            == 403
        )


@pytest.mark.asyncio
async def test_gateway_network_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(
        httpx.AsyncClient, "request", AsyncMock(side_effect=httpx.ConnectError("offline"))
    )
    with pytest.raises(HTTPException) as error:
        await identity_gateway.gateway_request("/v1/auth/me", tenant="acme", token="never-log-this")
    assert error.value.status_code == 503
    assert "never-log-this" not in str(error.value)


@pytest.mark.asyncio
async def test_gateway_company_provisioning_and_postgres_isolation(monkeypatch):
    """Use a throwaway database and a role that cannot bypass RLS."""
    import os
    import subprocess
    import sys
    from urllib.parse import urlsplit, urlunsplit

    import asyncpg
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.project import Project
    from app.tenancy import workspace_context

    admin_url = os.environ.get("SAAS_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set SAAS_TEST_ADMIN_URL for isolated PostgreSQL company tests")
    suffix = uuid.uuid4().hex[:12]
    database, role = f"aoi_gateway_test_{suffix}", f"aoi_gateway_role_{suffix}"
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    parsed = urlsplit(admin_url)
    target = urlunsplit(parsed._replace(path="/" + database))
    engine = owner = None
    try:
        env = {
            **os.environ,
            "DATABASE_URL": target.replace("postgresql://", "postgresql+asyncpg://"),
            "ENVIRONMENT": "development",
            "AUTH_MODE": "development",
            "AUTH_PROVIDER": "oidc",
        }
        result = subprocess.run(  # noqa: S603 -- fixed interpreter/module and isolated test environment
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            cwd=os.path.dirname(os.path.dirname(__file__)),
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
        for key, value in {
            "AUTH_MODE": "oidc",
            "AUTH_PROVIDER": "gateway",
            "AUTH_ISSUER": "https://gateway.local",
            "AUTH_AUDIENCE": "identity-gateway",
            "AUTH_JWKS_URL": "http://gateway:8080/.well-known/jwks.json",
            "IDENTITY_GATEWAY_URL": "http://gateway:8080",
            "ENVIRONMENT": "development",
            "CONNECTOR_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        }.items():
            monkeypatch.setenv(key, value)
        base_settings.cache_clear()
        runtime_url = (
            f"postgresql+asyncpg://{role}:test-only-password@"
            f"{parsed.hostname}:{parsed.port or 5432}/{database}"
        )
        engine = create_async_engine(runtime_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(auth, "SessionLocal", factory)
        a, b = uuid.uuid4(), uuid.uuid4()
        identities = {
            "owner-a": identity_gateway.GatewayIdentity(
                str(uuid.uuid4()), a, "company-a", "owner", 9999999999
            ),
            "member-a": identity_gateway.GatewayIdentity(
                str(uuid.uuid4()), a, "company-a", "viewer", 9999999999
            ),
            "owner-b": identity_gateway.GatewayIdentity(
                str(uuid.uuid4()), b, "company-b", "owner", 9999999999
            ),
        }

        async def verified(token):
            return identities[token]

        monkeypatch.setattr(identity_gateway, "verify_gateway_identity", verified)
        owner_a = await auth.authenticate("owner-a")
        member_a = await auth.authenticate("member-a")
        owner_b = await auth.authenticate("owner-b")
        assert owner_a.workspace_id == member_a.workspace_id
        assert owner_a.workspace_id != owner_b.workspace_id
        assert member_a.role == "viewer"
        with pytest.raises(HTTPException) as error:
            await auth.authenticate("owner-a", str(owner_b.workspace_id))
        assert error.value.status_code == 403
        for principal, name in [(owner_a, "Private A"), (owner_b, "Private B")]:
            context = workspace_context.set(principal.workspace_id)
            try:
                async with factory() as db:
                    db.add(Project(name=name, slug="same-slug"))
                    await db.commit()
            finally:
                workspace_context.reset(context)
        context = workspace_context.set(owner_a.workspace_id)
        try:
            async with factory() as db:
                assert [project.name for project in (await db.scalars(select(Project))).all()] == [
                    "Private A"
                ]
                assert await db.scalar(text("SELECT count(*) FROM projects")) == 1
                assert (
                    await db.scalar(
                        text("SELECT count(*) FROM projects WHERE workspace_id=:other"),
                        {"other": owner_b.workspace_id},
                    )
                    == 0
                )
        finally:
            workspace_context.reset(context)
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM projects")) == 0
    finally:
        base_settings.cache_clear()
        if engine:
            await engine.dispose()
        if owner:
            await owner.close()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
