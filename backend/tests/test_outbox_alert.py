"""Webhook outbox alerting, tested without a database or Slack."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.dialects import postgresql

from app.api import integrations
from app.config import Settings
from app.integrations import outbox_alert
from app.integrations.outbox_alert import OutboxHealth


@pytest.fixture
def settings(monkeypatch):
    config = Settings(_env_file=None)
    config.webhook_url = SecretStr("https://example.com/hook")
    config.webhook_secret = SecretStr("x" * 32)
    monkeypatch.setattr(outbox_alert, "get_settings", lambda: config)
    monkeypatch.setattr(integrations, "get_settings", lambda: config)
    outbox_alert._open_alerts.clear()
    return config


@pytest.fixture
def slack(monkeypatch):
    adapter = MagicMock(is_enabled=lambda: True, send_reminder=AsyncMock())
    monkeypatch.setattr(outbox_alert, "SlackAdapter", lambda: adapter)
    return adapter


def health(monkeypatch, **kwargs):
    value = OutboxHealth(
        **{"pending": 0, "failed": 0, "overdue": 0, "oldest_overdue_seconds": None, **kwargs}
    )
    monkeypatch.setattr(outbox_alert, "outbox_health", AsyncMock(return_value=value))
    return value


async def test_healthy_outbox_raises_nothing(settings, slack, monkeypatch):
    health(monkeypatch, pending=3)
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is False
    slack.send_reminder.assert_not_called()


async def test_failed_deliveries_alert_once_per_cooldown(settings, slack, monkeypatch):
    health(monkeypatch, failed=2)
    clock = [1000.0]
    monkeypatch.setattr(outbox_alert.time, "monotonic", lambda: clock[0])
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is True
    text = slack.send_reminder.await_args.args[0]
    assert "2 failed webhook deliveries" in text and "docs/runbook.md#webhook-outbox" in text
    clock[0] += settings.outbox_alert_cooldown_seconds - 1
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is False
    clock[0] += 2
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is True
    assert slack.send_reminder.await_count == 2


async def test_overdue_pending_means_worker_is_behind(settings, slack, monkeypatch):
    health(monkeypatch, pending=5, overdue=5, oldest_overdue_seconds=2 * 3600)
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is True
    text = slack.send_reminder.await_args.args[0]
    assert "5 pending deliveries are overdue by up to 120 minutes" in text


async def test_failed_below_threshold_does_not_alert(settings, slack, monkeypatch):
    settings.outbox_alert_failed_threshold = 3
    health(monkeypatch, failed=2)
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is False


async def test_recovery_message_is_sent_once(settings, slack, monkeypatch):
    health(monkeypatch, failed=1)
    await outbox_alert.check_outbox_alerts(MagicMock())
    health(monkeypatch)
    await outbox_alert.check_outbox_alerts(MagicMock())
    await outbox_alert.check_outbox_alerts(MagicMock())
    texts = [call.args[0] for call in slack.send_reminder.await_args_list]
    assert len(texts) == 2 and "recovered" in texts[1]


async def test_no_webhook_destination_skips_the_query(settings, slack, monkeypatch):
    settings.webhook_url = SecretStr("")
    probe = AsyncMock()
    monkeypatch.setattr(outbox_alert, "outbox_health", probe)
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is False
    probe.assert_not_called()


async def test_slack_failure_never_stops_the_scheduler(settings, slack, monkeypatch):
    slack.send_reminder.side_effect = RuntimeError("slack down")
    health(monkeypatch, failed=1)
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is True


async def test_alert_still_logs_when_slack_is_not_configured(settings, monkeypatch):
    monkeypatch.setattr(outbox_alert, "SlackAdapter", lambda: MagicMock(is_enabled=lambda: False))
    logged = MagicMock()
    monkeypatch.setattr(outbox_alert, "logger", logged)
    health(monkeypatch, failed=1)
    assert await outbox_alert.check_outbox_alerts(MagicMock()) is True
    assert logged.error.call_args.args[0] == "outbox.unhealthy"


async def test_health_query_is_valid_postgres_and_computes_age(settings):
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    oldest = now - timedelta(minutes=45)
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(one=lambda: (4, 1, 2, oldest)))
    result = await outbox_alert.outbox_health(session, now)
    assert result == OutboxHealth(pending=4, failed=1, overdue=2, oldest_overdue_seconds=2700)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.count("FILTER (WHERE") == 4 and "webhook_deliveries" in sql


async def test_health_endpoint_reports_problems(settings, monkeypatch):
    value = OutboxHealth(pending=0, failed=1, overdue=0, oldest_overdue_seconds=None)
    monkeypatch.setattr(integrations, "outbox_health", AsyncMock(return_value=value))
    body = await integrations.webhook_health(MagicMock())
    assert body["failed"] == 1
    assert body["problems"] == ["1 failed webhook deliveries need a manual retry"]


async def test_worker_tick_runs_the_outbox_check(monkeypatch):
    from app.workers import scheduler

    calls = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    for name in ("sync_task_statuses", "deliver_webhooks", "check_outbox_alerts"):

        async def op(session, _name=name):
            calls.append(_name)

        op.__name__ = name
        monkeypatch.setattr(scheduler, name, op)
    monkeypatch.setattr(scheduler, "SessionLocal", FakeSession)
    await scheduler._run_integrations()
    assert calls == ["sync_task_statuses", "deliver_webhooks", "check_outbox_alerts"]
