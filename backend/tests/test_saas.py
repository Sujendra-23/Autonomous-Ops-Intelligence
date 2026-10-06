"""Security regressions independent of external identity/provider accounts."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.testclient import TestClient

from app import auth
from app.config import Settings, base_settings
from app.security import SecurityMiddleware
from app.tenancy import principal_context, workspace_context, workspace_settings


@pytest.fixture
def saas(monkeypatch):
    for key, value in {
        "AUTH_MODE": "oidc",
        "AUTH_ISSUER": "https://identity.example/",
        "AUTH_AUDIENCE": "ops-api",
        "AUTH_JWKS_URL": "https://identity.example/keys",
        "CONNECTOR_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(key, value)
    base_settings.cache_clear()
    yield
    base_settings.cache_clear()


@pytest.fixture
def signed_tokens(saas, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="test", use="sig")
    monkeypatch.setattr(auth, "_jwks", {"test": public})
    monkeypatch.setattr(auth, "_jwks_until", float("inf"))

    def issue(**updates):
        now = datetime.now(UTC)
        claims = {
            "sub": "customer-123",
            "iss": "https://identity.example/",
            "aud": "ops-api",
            "iat": now,
            "exp": now + timedelta(minutes=10),
        }
        claims.update(updates)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})

    return issue


@pytest.mark.asyncio
async def test_valid_oidc_identity(signed_tokens):
    assert await auth.verify_identity(signed_tokens()) == "customer-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "wrong"},
        {"iss": "https://attacker.example/"},
        {"exp": 1},
        {"sub": ""},
        {"iat": datetime.now(UTC) + timedelta(days=1)},
        {"exp": None},
    ],
)
async def test_oidc_rejects_wrong_claims(signed_tokens, claims):
    with pytest.raises(HTTPException) as error:
        await auth.verify_identity(signed_tokens(**claims))
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_symmetric_algorithm(saas):
    token = jwt.encode({"sub": "attacker"}, "x" * 32, algorithm="HS256", headers={"kid": "test"})
    with pytest.raises(HTTPException):
        await auth.verify_identity(token)


def test_production_refuses_development_auth():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", auth_mode="development")


def test_connectors_never_fall_back_to_server_credentials(saas, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "server-secret")
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "server-channel")
    base_settings.cache_clear()
    assert base_settings().slack_enabled
    assert not workspace_settings(None).slack_enabled


def test_workspace_connector_ciphertext_isolated(saas):
    cipher = Fernet(base_settings().connector_encryption_key.get_secret_value().encode())
    stored = cipher.encrypt(
        json.dumps(
            {"slack_bot_token": "workspace-secret", "slack_default_channel": "C123"}
        ).encode()
    ).decode()
    assert "workspace-secret" not in stored
    assert workspace_settings(stored).slack_enabled
    assert not workspace_settings(None).slack_enabled


class FakeRedis:
    def __init__(self, count=1):
        self.count = count

    async def eval(self, *args):
        return self.count

    async def set(self, *args, **kwargs):
        return True


def protected_app(monkeypatch, role="member", count=1):
    import app.security as security

    p = auth.Principal(uuid.uuid4(), uuid.uuid4(), role, None)
    monkeypatch.setattr(security, "authenticate", AsyncMock(return_value=p))
    app = FastAPI()

    @app.get("/api/private")
    def private():
        return {"workspace_id": str(workspace_context.get())}

    @app.post("/api/private")
    def write():
        return {"status": "saved"}

    middleware = SecurityMiddleware(app)
    middleware.redis = FakeRedis(count)
    return TestClient(middleware), p


def test_reads_require_authentication(saas, monkeypatch):
    client, _ = protected_app(monkeypatch)
    assert client.get("/api/private").status_code == 401


def test_request_context_is_reset(saas, monkeypatch):
    client, p = protected_app(monkeypatch)
    response = client.get("/api/private", headers={"Authorization": "Bearer verified"})
    assert response.json()["workspace_id"] == str(p.workspace_id)
    assert response.headers["cache-control"] == "no-store"
    assert workspace_context.get() is None
    assert principal_context.get() is None


def test_viewer_cannot_mutate(saas, monkeypatch):
    client, _ = protected_app(monkeypatch, role="viewer")
    assert client.post("/api/private", headers={"X-API-Key": "aoi_valid"}).status_code == 403


def test_rate_limit_blocks_before_route(saas, monkeypatch):
    client, _ = protected_app(monkeypatch, count=121)
    assert client.get("/api/private", headers={"X-API-Key": "aoi_valid"}).status_code == 429


def test_oversized_request_rejected(saas, monkeypatch):
    client, _ = protected_app(monkeypatch)
    response = client.post(
        "/api/private", headers={"X-API-Key": "aoi_valid", "Content-Length": "999999999"}
    )
    assert response.status_code == 413


def test_extension_token_cannot_manage_accounts(saas, monkeypatch):
    client, _ = protected_app(monkeypatch)
    response = client.get("/api/account/tokens", headers={"X-API-Key": "aoi_valid"})
    assert response.status_code == 403


def test_workspace_administrator_required_for_connectors(saas):
    from app.api.account import administrator

    p = auth.Principal(uuid.uuid4(), uuid.uuid4(), "member", None)
    token = principal_context.set(p)
    try:
        with pytest.raises(HTTPException) as error:
            administrator()
        assert error.value.status_code == 403
    finally:
        principal_context.reset(token)


def test_websocket_authentication_frame_sets_workspace(saas, monkeypatch):
    import app.security as security

    p = auth.Principal(uuid.uuid4(), uuid.uuid4(), "member", None)
    monkeypatch.setattr(security, "authenticate", AsyncMock(return_value=p))
    from fastapi import WebSocket

    app = FastAPI()

    async def echo(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"workspace_id": str(workspace_context.get())})
        await websocket.close()

    app.add_api_websocket_route("/api/live/ws/test", echo)
    boundary = SecurityMiddleware(app)
    boundary.redis = FakeRedis()
    with TestClient(boundary).websocket_connect("/api/live/ws/test") as websocket:
        websocket.send_json({"type": "auth", "token": "aoi_valid"})
        assert websocket.receive_json()["workspace_id"] == str(p.workspace_id)
    assert workspace_context.get() is None


def test_websocket_rejects_audio_before_auth(saas, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    client, _ = protected_app(monkeypatch)
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/live/ws/test") as websocket:
            websocket.send_bytes(b"audio")
            websocket.receive_json()
    assert error.value.code == 1008
