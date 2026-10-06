"""Request and worker workspace context; never share mutable configuration."""

from contextvars import ContextVar
from uuid import UUID

LEGACY_WORKSPACE = UUID("00000000-0000-0000-0000-000000000001")
workspace_context: ContextVar[UUID | None] = ContextVar("workspace", default=None)
settings_context: ContextVar = ContextVar("workspace_settings", default=None)
principal_context: ContextVar = ContextVar("principal", default=None)

CONNECTOR_FIELDS = frozenset(
    {
        "notion_api_key",
        "notion_parent_page_id",
        "linear_api_key",
        "linear_team_id",
        "jira_base_url",
        "jira_email",
        "jira_api_token",
        "jira_project_key",
        "slack_bot_token",
        "slack_default_channel",
        "discord_webhook_url",
        "teams_webhook_url",
        "google_calendar_client_id",
        "google_calendar_client_secret",
        "google_calendar_refresh_token",
        "google_calendar_id",
        "task_sync_enabled",
        "linear_status_map",
        "jira_status_map",
        "webhook_url",
        "webhook_secret",
        "webhook_events",
    }
)


def workspace_settings(ciphertext: str | None):
    import json

    from cryptography.fernet import Fernet

    from app.config import Settings, base_settings

    base = base_settings()
    values = base.model_dump()
    defaults = Settings.model_fields
    for field in CONNECTOR_FIELDS:
        values[field] = defaults[field].get_default(call_default_factory=True)
    if ciphertext:
        decoded = json.loads(
            Fernet(base.connector_encryption_key.get_secret_value().encode()).decrypt(
                ciphertext.encode()
            )
        )
        if set(decoded) - CONNECTOR_FIELDS:
            raise ValueError("Unsupported connector configuration")
        values.update(decoded)
    return Settings.model_validate(values)
