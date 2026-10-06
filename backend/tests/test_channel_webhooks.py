"""Outbound connector contracts; every provider request uses MockTransport."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations import channel_webhooks
from app.integrations.channel_webhooks import DiscordAdapter, TeamsAdapter
from app.integrations.dispatcher import IntegrationDispatcher
from app.schemas.extraction import ExtractionResult

DISCORD_URL = "https://discord.com/api/webhooks/123/fake-secret?thread_id=456"
TEAMS_URL = "https://example.logic.azure.com/workflows/test/triggers/manual/invoke?sig=fake-secret"


def configure(monkeypatch, **kwargs):
    settings = Settings(_env_file=None, **kwargs)
    monkeypatch.setattr(channel_webhooks, "get_settings", lambda: settings)
    return settings


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


async def publish(adapter, summary="We agreed on the rollout."):
    return await adapter.post_summary(
        SimpleNamespace(title="API review"),
        SimpleNamespace(name="Migration"),
        ExtractionResult(summary=summary),
    )


async def test_disabled_connectors_do_not_send(monkeypatch):
    settings = configure(monkeypatch)
    assert not settings.discord_enabled and not settings.teams_enabled
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("Unexpected request"))
    for adapter in (DiscordAdapter(), TeamsAdapter()):
        result = await publish(adapter)
        assert not result.success and result.detail == "disabled"


async def test_discord_payload_confirms_delivery_and_disables_mentions(monkeypatch):
    configure(monkeypatch, discord_webhook_url=DISCORD_URL)

    def handle(request):
        assert request.url.params["wait"] == "true"
        assert request.url.params["thread_id"] == "456"
        body = json.loads(request.content)
        assert body["allowed_mentions"] == {"parse": []}
        assert len(body["content"]) <= 2000
        assert "API review" in body["content"] and "Migration" in body["content"]
        return httpx.Response(200, json={"id": "message-1"})

    transport(monkeypatch, handle)
    result = await publish(DiscordAdapter(), "@everyone " + "x" * 10000)
    assert result.success and result.external_id == "message-1"


async def test_teams_posts_adaptive_card_preserving_signed_query(monkeypatch):
    configure(monkeypatch, teams_webhook_url=TEAMS_URL)

    def handle(request):
        assert request.url.params["sig"] == "fake-secret"
        body = json.loads(request.content)
        assert body["type"] == "message"
        attachment = body["attachments"][0]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
        card = attachment["content"]
        assert card["type"] == "AdaptiveCard" and card["body"][0]["wrap"] is True
        assert len(request.content) < 28000
        return httpx.Response(202)

    transport(monkeypatch, handle)
    assert (await publish(TeamsAdapter(), "🎉" * 10000)).success


@pytest.mark.parametrize(
    "adapter_cls,setting,url",
    [
        (DiscordAdapter, "discord_webhook_url", DISCORD_URL),
        (TeamsAdapter, "teams_webhook_url", TEAMS_URL),
    ],
)
@pytest.mark.parametrize("status", [302, 400, 429, 500])
async def test_provider_failures_are_sanitized(monkeypatch, adapter_cls, setting, url, status):
    configure(monkeypatch, **{setting: url})
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            status, text="fake-secret", headers={"Location": "https://elsewhere.example"}
        ),
    )
    result = await publish(adapter_cls())
    assert not result.success and result.detail == f"HTTP {status}"
    assert "fake-secret" not in repr(result)


async def test_network_errors_do_not_expose_webhook_credentials(monkeypatch):
    configure(monkeypatch, discord_webhook_url=DISCORD_URL)

    def fail(request):
        raise httpx.ConnectError(f"Failed to connect to {request.url}", request=request)

    transport(monkeypatch, fail)
    result = await publish(DiscordAdapter())
    assert not result.success and result.detail == "Network error"
    assert "fake-secret" not in repr(result)


async def test_discord_requires_message_confirmation(monkeypatch):
    configure(monkeypatch, discord_webhook_url=DISCORD_URL)
    transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert not (await publish(DiscordAdapter())).success


@pytest.mark.parametrize(
    "setting,url",
    [
        ("discord_webhook_url", "http://discord.com/api/webhooks/123/token"),
        ("discord_webhook_url", "https://discord.com.evil.example/api/webhooks/123/token"),
        ("discord_webhook_url", "https://discord.com/api/users/123"),
        ("teams_webhook_url", "http://example.logic.azure.com/workflow"),
        ("teams_webhook_url", "https://user:password@example.logic.azure.com/workflow"),
        ("teams_webhook_url", TEAMS_URL + "#fragment"),
    ],
)
def test_invalid_destinations_are_rejected(setting, url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{setting: url})


async def test_dispatcher_invokes_both_connectors_after_processing(monkeypatch):
    configure(monkeypatch, discord_webhook_url=DISCORD_URL, teams_webhook_url=TEAMS_URL)
    for adapter_cls in (DiscordAdapter, TeamsAdapter):
        monkeypatch.setattr(
            adapter_cls,
            "post_summary",
            AsyncMock(return_value=channel_webhooks.DispatchResult(adapter_cls.name, True)),
        )
    dispatcher = IntegrationDispatcher()
    assert {adapter.name for adapter in dispatcher._notifiers} >= {"discord", "teams"}
    dispatcher._notifiers = [
        adapter for adapter in dispatcher._notifiers if adapter.name in {"discord", "teams"}
    ]
    session = MagicMock()
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=rows)
    session.commit = AsyncMock()
    outcomes = await dispatcher.publish(
        session,
        SimpleNamespace(id="transcript", title="Review"),
        None,
        ExtractionResult(summary="Reviewed the API."),
    )
    assert {outcome.adapter for outcome in outcomes} >= {"discord", "teams"}
    assert DiscordAdapter.post_summary.await_count == 1
    assert TeamsAdapter.post_summary.await_count == 1


def test_summary_includes_categories_and_bounds_long_content():
    result = ExtractionResult(
        summary="Migration reviewed.",
        tasks=[
            {
                "title": "Run validation",
                "owner": "Morgan",
                "due_date": "2026-10-09T12:00:00Z",
                "source_quote": "Run validation.",
                "confidence": 0.9,
            }
        ],
        decisions=[{"summary": "Roll out Friday", "source_quote": "Friday", "confidence": 0.9}],
        risks=[{"title": "Validation may fail", "source_quote": "May fail", "confidence": 0.9}],
        blockers=[
            {"summary": "Waiting for access", "source_quote": "No access", "confidence": 0.9}
        ],
    )
    text = channel_webhooks.summary_text(SimpleNamespace(title="Review"), None, result, 2000)
    for expected in ("Tasks", "Morgan", "2026-10-09", "Decisions", "Risks", "Blockers"):
        assert expected in text
    result.tasks[0].title = "x" * 250
    result.tasks *= 10
    result.summary = "x" * 1200
    text = channel_webhooks.summary_text(SimpleNamespace(title="Review"), None, result, 2000)
    assert len(text) <= 2000 and text.endswith("More items in AOI.")


async def test_status_reports_flags_without_returning_secrets(monkeypatch):
    from app.api import integrations

    settings = configure(monkeypatch, discord_webhook_url=DISCORD_URL, teams_webhook_url=TEAMS_URL)
    monkeypatch.setattr(integrations, "get_settings", lambda: settings)
    status = await integrations.integration_status()
    assert status["discord"] is True and status["teams"] is True
    assert "fake-secret" not in json.dumps(status)
