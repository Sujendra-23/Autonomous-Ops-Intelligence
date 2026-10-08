"""Centralised typed configuration loaded from environment variables.

We use pydantic-settings so config errors surface at startup, not at request time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # ---- App ----
    environment: Literal["development", "staging", "production"] = "development"
    auth_mode: Literal["development", "oidc"] = "development"
    auth_issuer: str = ""
    auth_audience: str = ""
    auth_jwks_url: str = ""
    connector_allowed_webhook_hosts: str = ""
    connector_encryption_key: SecretStr = SecretStr("")
    platform_paid_operations_per_day: int = Field(default=1000, ge=1)
    paid_operations_per_day: int = Field(default=100, ge=1)
    live_session_max_seconds: int = Field(default=7200, ge=60, le=14400)
    request_limit_per_minute: int = Field(default=120, ge=1)
    max_request_bytes: int = Field(default=104857600, ge=1024)
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    ingest_api_key: SecretStr = SecretStr("")
    # Analytics credentials are independent of ingestion and fail closed when unset.
    intelligence_api_key: SecretStr = SecretStr("")
    intelligence_owner_api_key: SecretStr = SecretStr("")
    intelligence_database_url: SecretStr = SecretStr("")
    intelligence_owner_database_url: SecretStr = SecretStr("")

    # ---- Database ----
    database_url: str = "postgresql+asyncpg://aoi:aoi@localhost:5432/aoi"
    redis_url: str = "redis://localhost:6379/0"

    # ---- LLM ----
    # Default = Anthropic with Haiku 4.5, the cheapest Claude. Switch
    # ANTHROPIC_MODEL to Sonnet/Opus if extraction quality demands it.
    #
    # API-key fields are SecretStr so they never appear in logs, repr,
    # tracebacks, or serialized settings dumps — even by accident.
    llm_provider: Literal["openai", "anthropic"] = "anthropic"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-haiku-4-5-20251001"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_provider: Literal["openai", "local"] = "openai"

    # ---- Notion ----
    notion_api_key: SecretStr = SecretStr("")
    notion_parent_page_id: str = ""

    # ---- Linear ----
    linear_api_key: SecretStr = SecretStr("")
    linear_team_id: str = ""

    # ---- Jira ----
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_project_key: str = ""

    # ---- Slack ----
    slack_bot_token: SecretStr = SecretStr("")
    slack_default_channel: str = ""

    # Channel webhook URLs contain credentials and remain on the server.
    discord_webhook_url: SecretStr = SecretStr("")
    teams_webhook_url: SecretStr = SecretStr("")

    # Optional integrations: credentials remain on the server.
    google_calendar_client_id: str = ""
    google_calendar_client_secret: SecretStr = SecretStr("")
    google_calendar_refresh_token: SecretStr = SecretStr("")
    google_calendar_id: str = "primary"
    task_sync_enabled: bool = False
    # AOI status -> provider workflow status ID. Needed for custom/blocked states.
    linear_status_map: dict[str, str] = Field(default_factory=dict)
    jira_status_map: dict[str, str] = Field(default_factory=dict)
    integration_interval_seconds: int = Field(default=60, ge=10)
    webhook_url: SecretStr = SecretStr("")
    webhook_secret: SecretStr = SecretStr("")
    webhook_events: list[str] = Field(
        default_factory=lambda: [
            "meeting.completed",
            "task.created",
            "task.updated",
        ]
    )

    @property
    def google_calendar_enabled(self) -> bool:
        return bool(
            self.google_calendar_client_id
            and self.google_calendar_client_secret.get_secret_value()
            and self.google_calendar_refresh_token.get_secret_value()
        )

    @model_validator(mode="after")
    def validate_integrations(self) -> Settings:
        from urllib.parse import urlsplit

        url = self.webhook_url.get_secret_value()
        if url:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("WEBHOOK_URL must be HTTPS without userinfo or fragment")
            if len(self.webhook_secret.get_secret_value()) < 32:
                raise ValueError("WEBHOOK_SECRET must contain at least 32 characters")
        for name in ("discord_webhook_url", "teams_webhook_url"):
            destination = getattr(self, name).get_secret_value()
            if not destination:
                continue
            parsed = urlsplit(destination)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError(f"{name.upper()} must be HTTPS without userinfo or fragment")
            if name == "discord_webhook_url":
                import re

                if parsed.hostname not in {
                    "discord.com",
                    "canary.discord.com",
                    "ptb.discord.com",
                } or not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+/?", parsed.path):
                    raise ValueError("DISCORD_WEBHOOK_URL must be a Discord channel webhook URL")
        statuses = {"open", "in_progress", "blocked", "done", "cancelled"}
        for mapping in (self.linear_status_map, self.jira_status_map):
            if set(mapping) - statuses or any(not value for value in mapping.values()):
                raise ValueError("Status maps require valid AOI statuses and nonempty IDs")
            if len(set(mapping.values())) != len(mapping):
                raise ValueError("Each provider status ID must map to only one AOI status")
        if self.environment != "development" and self.auth_mode != "oidc":
            raise ValueError("Staging and production require AUTH_MODE=oidc")
        if self.auth_mode == "oidc":
            if not self.auth_issuer.startswith("https://") or not self.auth_audience:
                raise ValueError("OIDC requires an HTTPS issuer and audience")
            if not self.auth_jwks_url.startswith("https://"):
                raise ValueError("OIDC requires an HTTPS JWKS URL")
            from cryptography.fernet import Fernet

            Fernet(self.connector_encryption_key.get_secret_value().encode())
            if "*" in self.cors_origin_list:
                raise ValueError("Explicit CORS origins are required")
        return self

    # ---- Monitor ----
    monitor_interval_seconds: int = Field(default=900, ge=30)
    overdue_grace_days: int = Field(default=0, ge=0)
    stall_days: int = Field(default=7, ge=1)

    # ---- Live note-taker (streaming STT) ----
    # Provider for real-time transcription of meeting audio. "none" disables it
    # (the WebSocket still runs but produces no transcript). "openai" uses the
    # Realtime transcription API and reuses OPENAI_API_KEY above.
    stt_provider: Literal["deepgram", "openai", "none"] = "none"
    deepgram_api_key: SecretStr = SecretStr("")
    openai_realtime_model: str = "gpt-4o-transcribe"
    # Extraction cadence during a live meeting (see services/live_session.py).
    live_min_chars: int = Field(default=180, ge=20)
    live_min_interval_seconds: float = Field(default=8.0, ge=1.0)
    live_max_interval_seconds: float = Field(default=30.0, ge=5.0)

    @property
    def stt_enabled(self) -> bool:
        if self.stt_provider == "deepgram":
            return bool(self.deepgram_api_key.get_secret_value())
        if self.stt_provider == "openai":
            return bool(self.openai_api_key.get_secret_value())
        return False

    # ---- Cross-meeting intelligence ----
    # When true, the extractor is shown the project's existing open tasks /
    # decisions / blockers so it can emit status updates instead of duplicates.
    cross_meeting_context: bool = True
    # Max items of each kind injected into the prompt (bounds token cost).
    context_max_tasks: int = Field(default=25, ge=1)
    context_max_decisions: int = Field(default=10, ge=1)
    context_max_blockers: int = Field(default=10, ge=1)
    # Normalised-title similarity (0..1) above which a new task is treated as a
    # duplicate of an existing open one. Higher = more conservative.
    dedup_title_threshold: float = Field(default=0.82, ge=0.0, le=1.0)

    # ---- Computed flags ----
    # SecretStr is always truthy as an object, so we must check the underlying
    # value explicitly via get_secret_value() rather than `if x:` on the field.
    @property
    def notion_enabled(self) -> bool:
        return bool(self.notion_api_key.get_secret_value() and self.notion_parent_page_id)

    @property
    def linear_enabled(self) -> bool:
        return bool(self.linear_api_key.get_secret_value() and self.linear_team_id)

    @property
    def jira_enabled(self) -> bool:
        return bool(
            self.jira_base_url and self.jira_api_token.get_secret_value() and self.jira_email
        )

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token.get_secret_value() and self.slack_default_channel)

    @property
    def discord_enabled(self) -> bool:
        return bool(self.discord_webhook_url.get_secret_value())

    @property
    def teams_enabled(self) -> bool:
        return bool(self.teams_webhook_url.get_secret_value())

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        # Tolerate the standard "postgresql://" form by upgrading it to the async driver
        # SQLAlchemy actually needs ("postgresql+asyncpg://...").
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value


@lru_cache
def base_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    from app.tenancy import settings_context

    return settings_context.get() or base_settings()


get_settings.cache_clear = base_settings.cache_clear
