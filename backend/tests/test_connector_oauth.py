"""OAuth contracts: authorization, replay protection, encryption, tenant ownership and refresh."""

import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app import config
from app.api import connectors
from app.auth import Principal
from app.integrations import oauth
from app.models.account import Membership, Workspace
from app.tenancy import principal_context, settings_context, workspace_context, workspace_settings


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, key):
        return self.data.get(key)

    async def getdel(self, key):
        return self.data.pop(key, None)

    async def delete(self, key):
        self.data.pop(key, None)

    async def set(self, key, value, ex=None):
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ex

    def pipeline(self, transaction=True):
        redis = self

        class Pipeline:
            def __init__(self):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def set(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return self

            async def execute(self):
                for args, kwargs in self.calls:
                    await redis.set(*args, **kwargs)

        return Pipeline()


@pytest.fixture
def flow(monkeypatch):
    settings = config.Settings(
        _env_file=None,
        auth_mode="oidc",
        auth_issuer="https://identity.example/",
        auth_audience="aoi",
        auth_jwks_url="https://identity.example/keys",
        connector_encryption_key=Fernet.generate_key().decode(),
        connector_oauth_redirect_base="https://ops.example",
        **{name + "_client_id": "client" for name in oauth.PROVIDERS},
        **{name + "_client_secret": "private-client-secret" for name in oauth.PROVIDERS},
    )
    monkeypatch.setattr(config, "base_settings", lambda: settings)
    monkeypatch.setattr(oauth, "base_settings", lambda: settings)
    monkeypatch.setattr(connectors, "base_settings", lambda: settings)
    redis = MemoryRedis()
    monkeypatch.setattr(connectors.Redis, "from_url", lambda *_: redis)
    p = Principal(uuid.uuid4(), uuid.uuid4(), "owner", None)
    workspace = Workspace(id=p.workspace_id, name="Test workspace")
    membership = Membership(account_id=p.account_id, workspace_id=p.workspace_id, role="owner")

    async def get(model, identifier, **kwargs):
        if model is Workspace and identifier == p.workspace_id:
            return workspace
        if model is Membership:
            return membership
        return None

    db = MagicMock(get=AsyncMock(side_effect=get), commit=AsyncMock(), rollback=AsyncMock())
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=None)
    tokens = [
        (principal_context, principal_context.set(p)),
        (workspace_context, workspace_context.set(p.workspace_id)),
        (settings_context, settings_context.set(workspace_settings(None))),
    ]
    yield settings, redis, p, workspace, membership, db
    for variable, token in reversed(tokens):
        variable.reset(token)


async def begin(flow, provider="slack"):
    result = await connectors.start_connector(provider, flow[-1])
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    return result, state


def mock_slack(monkeypatch):
    monkeypatch.setattr(
        oauth,
        "exchange",
        AsyncMock(
            return_value={
                "access_token": "private-access",
                "refresh_token": "private-refresh",
                "expires_in": 3600,
            }
        ),
    )
    monkeypatch.setattr(
        oauth,
        "destinations",
        AsyncMock(
            return_value=[
                oauth.choice(
                    "C123",
                    "#ops",
                    {"slack_bot_token": "private-access", "slack_default_channel": "C123"},
                )
            ]
        ),
    )


@pytest.mark.parametrize("provider", oauth.PROVIDERS)
def test_authorization_uses_provider_page_and_registered_callback(flow, provider):
    params = parse_qs(urlsplit(oauth.authorization(provider, "random-state", "verifier")).query)
    assert params["state"] == ["random-state"]
    assert params["redirect_uri"] == [f"https://ops.example/oauth/connectors/{provider}/callback"]
    assert params["response_type"] == ["code"]
    assert "private-client-secret" not in oauth.authorization(provider, "state", "verifier")
    if provider in {"google_calendar", "linear", "teams"}:
        assert params["code_challenge_method"] == ["S256"]
        assert params["code_challenge"] != ["verifier"]


@pytest.mark.parametrize("provider", oauth.PROVIDERS)
async def test_token_exchange_is_server_side_and_refresh_uses_same_protocol(flow, provider):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url == oauth.endpoints(provider)[1]
        body = (
            json.loads(request.content)
            if provider in {"notion", "jira"}
            else {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        )
        assert body["grant_type"] in {"authorization_code", "refresh_token"}
        if body["grant_type"] == "authorization_code":
            assert body["code"] == "authorization-code"
            assert body["redirect_uri"] == oauth.redirect_uri(provider)
        else:
            assert body["refresh_token"] == "refresh"
        if provider in {"notion", "slack", "discord"}:
            assert request.headers["Authorization"].startswith("Basic ")
            assert "client_secret" not in body
        else:
            assert body["client_secret"] == "private-client-secret"
        return httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await oauth.exchange(client, provider, code="authorization-code", verifier="verifier")
        await oauth.exchange(client, provider, refresh_token="refresh")
    assert len(requests) == 2


async def test_callback_saves_encrypted_grant_and_status_hides_secrets(flow, monkeypatch):
    mock_slack(monkeypatch)
    result, state = await begin(flow)
    page = await connectors.oauth_callback("slack", state, "code", "", flow[-1])
    assert page.status_code == 200
    row = flow[3]
    assert "private-access" not in row.connector_ciphertext
    assert "private-refresh" not in row.connector_ciphertext
    saved = workspace_settings(row.connector_ciphertext)
    assert saved.slack_enabled
    assert oauth.grants(saved)["slack"]["access_token"] == "private-access"
    status = await connectors.connection_attempt(result["ticket"])
    assert status["status"] == "connected"
    ctx = settings_context.set(saved)
    try:
        catalog = await connectors.list_connectors()
    finally:
        settings_context.reset(ctx)
    assert catalog["slack"]["destination"] == "#ops"
    assert "private" not in json.dumps(catalog)
    assert "private" not in page.body.decode()
    assert page.headers["cache-control"] == "no-store"
    assert flow[1].ttls[oauth.ticket_key(result["ticket"])] == 600


async def test_callback_is_one_use(flow, monkeypatch):
    mock_slack(monkeypatch)
    _, state = await begin(flow)
    assert (
        await connectors.oauth_callback("slack", state, "code", "", flow[-1])
    ).status_code == 200
    assert (
        await connectors.oauth_callback("slack", state, "code", "", flow[-1])
    ).status_code == 400
    assert oauth.exchange.await_count == 1


async def test_mismatched_provider_does_not_consume_state(flow, monkeypatch):
    mock_slack(monkeypatch)
    _, state = await begin(flow)
    assert (
        await connectors.oauth_callback("linear", state, "code", "", flow[-1])
    ).status_code == 400
    oauth.exchange.assert_not_awaited()
    assert await flow[1].get(oauth.state_key(state))


async def test_denied_signin_preserves_existing_configuration(flow):
    before = (
        oauth.cipher()
        .encrypt(json.dumps({"slack_bot_token": "old", "slack_default_channel": "COLD"}).encode())
        .decode()
    )
    flow[3].connector_ciphertext = before
    result, state = await begin(flow)
    response = await connectors.oauth_callback("slack", state, "", "access_denied", flow[-1])
    assert response.status_code == 400
    assert flow[3].connector_ciphertext == before
    assert (await connectors.connection_attempt(result["ticket"]))["status"] == "failed"
    flow[-1].commit.assert_not_awaited()


async def test_unknown_and_expired_state_never_exchange(flow, monkeypatch):
    mock_slack(monkeypatch)
    response = await connectors.oauth_callback("slack", "unknown-state", "code", "", flow[-1])
    assert response.status_code == 400
    oauth.exchange.assert_not_awaited()


async def test_membership_removed_before_callback_rejects_grant(flow, monkeypatch):
    mock_slack(monkeypatch)
    result, state = await begin(flow)
    flow[4].role = "member"
    response = await connectors.oauth_callback("slack", state, "code", "", flow[-1])
    assert response.status_code == 400
    oauth.exchange.assert_not_awaited()
    assert (await connectors.connection_attempt(result["ticket"]))["status"] == "failed"


async def test_new_attempt_replaces_old_and_disconnect_invalidates_attempt(flow, monkeypatch):
    mock_slack(monkeypatch)
    _, old_state = await begin(flow)
    _, new_state = await begin(flow)
    assert (
        await connectors.oauth_callback("slack", old_state, "code", "", flow[-1])
    ).status_code == 400
    await connectors.disconnect("slack", flow[-1])
    assert (
        await connectors.oauth_callback("slack", new_state, "code", "", flow[-1])
    ).status_code == 400
    oauth.exchange.assert_not_awaited()
    assert not workspace_settings(flow[3].connector_ciphertext).slack_enabled


async def test_other_account_or_workspace_cannot_poll_attempt(flow):
    result, _ = await begin(flow)
    p = flow[2]
    for principal in [
        Principal(uuid.uuid4(), p.workspace_id, "owner", None),
        Principal(p.account_id, uuid.uuid4(), "owner", None),
    ]:
        ctx = principal_context.set(principal)
        try:
            with pytest.raises(HTTPException) as exc:
                await connectors.connection_attempt(result["ticket"])
            assert exc.value.status_code == 404
        finally:
            principal_context.reset(ctx)


async def test_non_admin_cannot_connect_or_select_or_disconnect(flow):
    p = flow[2]
    ctx = principal_context.set(Principal(p.account_id, p.workspace_id, "member", None))
    try:
        for work in [
            connectors.start_connector("slack", flow[-1]),
            connectors.select_destination("slack", connectors.Destination(id="C123"), flow[-1]),
            connectors.disconnect("slack", flow[-1]),
        ]:
            with pytest.raises(HTTPException) as exc:
                await work
            assert exc.value.status_code == 403
    finally:
        principal_context.reset(ctx)


async def test_multiple_destinations_require_explicit_choice_and_reject_unknown(flow, monkeypatch):
    mock_slack(monkeypatch)
    options = [
        oauth.choice(
            "t1",
            "Team one",
            {"linear_api_key": "private-access", "linear_team_id": "t1", "task_sync_enabled": True},
        ),
        oauth.choice(
            "t2",
            "Team two",
            {"linear_api_key": "private-access", "linear_team_id": "t2", "task_sync_enabled": True},
        ),
    ]
    monkeypatch.setattr(oauth, "destinations", AsyncMock(return_value=options))
    result, state = await begin(flow, "linear")
    assert (
        await connectors.oauth_callback("linear", state, "code", "", flow[-1])
    ).status_code == 200
    assert (await connectors.connection_attempt(result["ticket"]))["status"] == "select_destination"
    assert not workspace_settings(flow[3].connector_ciphertext).linear_enabled
    with pytest.raises(HTTPException) as exc:
        await connectors.select_destination(
            "linear", connectors.Destination(id="unrelated-team"), flow[-1]
        )
    assert exc.value.status_code == 409
    selected = await connectors.select_destination(
        "linear", connectors.Destination(id="t2"), flow[-1]
    )
    assert selected["status"] == "connected"
    settings = workspace_settings(flow[3].connector_ciphertext)
    assert settings.linear_team_id == "t2"
    assert settings.linear_enabled and settings.task_sync_enabled
    assert "private" not in json.dumps(selected)


async def test_expiring_tokens_refresh_and_persist_rotation_under_lock(flow, monkeypatch):
    import app.database as database

    metadata = {
        "linear": {
            "status": "connected",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": time.time() - 1,
        }
    }
    settings = workspace_settings(None)
    settings = connectors.apply_values(
        settings, {"linear_api_key": "old-access", "linear_team_id": "t1"}
    )
    oauth.save_settings(flow[3], settings, metadata)
    ctx = settings_context.set(settings)
    monkeypatch.setattr(database, "SessionLocal", lambda: flow[-1])
    monkeypatch.setattr(
        oauth,
        "exchange",
        AsyncMock(
            return_value={
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            }
        ),
    )
    try:
        assert await oauth.linear_authorization("old-access") == "Bearer rotated-access"
        assert await oauth.linear_authorization("old-access") == "Bearer rotated-access"
    finally:
        settings_context.reset(ctx)
    oauth.exchange.assert_awaited_once()
    saved = workspace_settings(flow[3].connector_ciphertext)
    assert oauth.grants(saved)["linear"]["refresh_token"] == "rotated-refresh"
    assert saved.linear_api_key.get_secret_value() == "rotated-access"
    flow[-1].get.assert_awaited_with(Workspace, flow[2].workspace_id, with_for_update=True)


async def test_provider_failure_does_not_expose_response_secrets(flow):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"error": "private-sentinel"})
        )
    ) as client:
        with pytest.raises(oauth.OAuthError) as exc:
            await oauth.exchange(client, "jira", code="code", verifier="verifier")
    assert "private-sentinel" not in str(exc.value)


async def test_missing_app_credentials_returns_setup_error_without_creating_attempt(flow):
    flow[0].slack_client_id = ""
    with pytest.raises(HTTPException) as exc:
        await connectors.start_connector("slack", flow[-1])
    assert exc.value.status_code == 503
    assert not flow[1].data


@pytest.mark.parametrize("provider", ["jira", "teams"])
async def test_unique_site_or_team_discovers_and_selects_actual_destination(flow, provider):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer access"
        if request.url.path.endswith("accessible-resources"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "cloud",
                        "name": "Example",
                        "url": "https://example.atlassian.net",
                        "scopes": ["write:jira-work"],
                    }
                ],
            )
        if request.url.path.endswith("project/search"):
            return httpx.Response(
                200, json={"isLast": True, "values": [{"key": "OPS", "name": "Operations"}]}
            )
        if request.url.path.endswith("joinedTeams"):
            return httpx.Response(200, json={"value": [{"id": "team", "displayName": "Team"}]})
        if request.url.path.endswith("channels"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "19:channel",
                            "displayName": "Meetings",
                            "membershipType": "standard",
                        },
                        {"id": "private", "displayName": "Private", "membershipType": "private"},
                    ]
                },
            )
        raise AssertionError(request.url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        entry = {
            "access_token": "access",
            "choices": await oauth.destinations(client, provider, "access"),
        }
        values = await oauth.advance(client, provider, entry)
    assert entry["status"] == "connected"
    assert len(seen) == 2
    if provider == "jira":
        assert values["jira_cloud_id"] == "cloud" and values["jira_project_key"] == "OPS"
    else:
        assert values["teams_team_id"] == "team" and values["teams_channel_id"] == "19:channel"


async def test_notion_discovery_uses_shared_roots_and_paginates(flow):
    pages = [
        {
            "id": "root",
            "parent": {"type": "workspace"},
            "properties": {"title": {"type": "title", "title": [{"plain_text": "Meeting notes"}]}},
        },
        {"id": "child", "parent": {"page_id": "root"}, "properties": {}},
    ]
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("start_cursor"):
            return httpx.Response(200, json={"results": [pages[1]], "has_more": False})
        return httpx.Response(
            200, json={"results": [pages[0]], "has_more": True, "next_cursor": "next"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        choices = await oauth.destinations(client, "notion", "access")
    assert len(requests) == 2
    assert [c["id"] for c in choices] == ["root"]
    assert choices[0]["values"]["notion_parent_page_id"] == "root"


async def test_discord_grant_uses_channel_webhook_and_google_requires_offline_access(flow):
    async with httpx.AsyncClient() as client:
        choices = await oauth.destinations(
            client,
            "discord",
            "access",
            {
                "webhook": {
                    "id": "123",
                    "token": "webhook-token",
                    "channel_id": "456",
                    "name": "Notes",
                }
            },
        )
        assert (
            choices[0]["values"]["discord_webhook_url"]
            == "https://discord.com/api/webhooks/123/webhook-token"
        )
        with pytest.raises(oauth.OAuthError, match="offline access"):
            await oauth.destinations(client, "google_calendar", "access", {})


async def test_notion_refreshes_on_401_when_provider_returns_no_expiry(flow, monkeypatch):
    import app.database as database

    settings = connectors.apply_values(
        workspace_settings(None), {"notion_api_key": "old", "notion_parent_page_id": "page"}
    )
    metadata = {
        "notion": {"status": "connected", "access_token": "old", "refresh_token": "refresh"}
    }
    oauth.save_settings(flow[3], settings, metadata)
    ctx = settings_context.set(settings)
    monkeypatch.setattr(database, "SessionLocal", lambda: flow[-1])
    monkeypatch.setattr(
        oauth,
        "exchange",
        AsyncMock(return_value={"access_token": "new", "refresh_token": "rotated"}),
    )
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(
            401 if request.headers["Authorization"] == "Bearer old" else 200, json={"id": "page"}
        )

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await oauth.notion_request(
                client,
                "POST",
                "https://api.notion.com/v1/pages",
                headers={"Authorization": "Bearer old"},
                json={},
            )
    finally:
        settings_context.reset(ctx)
    assert response.status_code == 200
    assert seen == ["Bearer old", "Bearer new"]
    assert (
        oauth.grants(workspace_settings(flow[3].connector_ciphertext))["notion"]["refresh_token"]
        == "rotated"
    )


async def test_teams_oauth_publishes_to_graph_with_encoded_channel(flow, monkeypatch):
    from app.integrations import channel_webhooks
    from app.models.transcript import Transcript
    from app.schemas.extraction import ExtractionResult

    settings = connectors.apply_values(
        workspace_settings(None),
        {"teams_team_id": "team", "teams_channel_id": "19:channel@thread.tacv2"},
    )
    ctx = settings_context.set(settings)
    monkeypatch.setattr(channel_webhooks, "access_token", AsyncMock(return_value="access"))
    original = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "graph.microsoft.com"
        assert "19%3Achannel%40thread.tacv2" in str(request.url)
        assert request.headers["Authorization"] == "Bearer access"
        assert json.loads(request.content)["body"]["contentType"] == "text"
        return httpx.Response(201, json={"id": "message"})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    try:
        outcome = await channel_webhooks.TeamsAdapter().post_summary(
            Transcript(title="Review"), None, ExtractionResult(summary="Discuss next steps")
        )
    finally:
        settings_context.reset(ctx)
    assert outcome.success and outcome.external_id == "message"
    assert len(seen) == 1


async def test_redis_outage_fails_signin_closed(flow, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    monkeypatch.setattr(
        flow[1], "pipeline", MagicMock(side_effect=RedisConnectionError("private-redis-url"))
    )
    with pytest.raises(HTTPException) as exc:
        await connectors.start_connector("slack", flow[-1])
    assert exc.value.status_code == 503
    assert "private-redis-url" not in str(exc.value.detail)


@pytest.mark.parametrize("provider", oauth.PROVIDERS)
async def test_missing_oauth_app_reports_same_safe_error_in_list_and_start(flow, provider):
    from pydantic import SecretStr

    setattr(flow[0], provider + "_client_secret", SecretStr(""))
    catalog = await connectors.list_connectors()
    assert catalog[provider]["oauth_available"] is False
    assert oauth.PROVIDERS[provider][0] in catalog[provider]["setup_error"]
    assert "client ID and secret" in catalog[provider]["setup_error"]
    with pytest.raises(HTTPException) as exc:
        await connectors.start_connector(provider, flow[-1])
    assert exc.value.status_code == 503
    assert exc.value.detail == catalog[provider]["setup_error"]
    assert "private-client-secret" not in json.dumps(catalog)
    flow[-1].get.assert_not_awaited()


def test_configured_app_reports_encryption_and_callback_setup_errors(flow):
    from pydantic import SecretStr

    key = flow[0].connector_encryption_key
    flow[0].connector_encryption_key = SecretStr("")
    assert "encryption" in oauth.setup_error("google_calendar")
    flow[0].connector_encryption_key = key
    flow[0].connector_oauth_redirect_base = "http://insecure.example"
    assert "callback" in oauth.setup_error("google_calendar")
    flow[0].connector_oauth_redirect_base = "https://ops.example"
    assert oauth.setup_error("google_calendar") is None
