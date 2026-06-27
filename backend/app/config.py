"""Centralised typed configuration loaded from environment variables.

We use pydantic-settings so config errors surface at startup, not at request time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    ingest_api_key: SecretStr = SecretStr("")

    # ---- Database ----
    database_url: str = (
        "postgresql+asyncpg://aoi:aoi@localhost:5432/aoi"
    )
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

    # ---- Monitor ----
    monitor_interval_seconds: int = Field(default=900, ge=30)
    overdue_grace_days: int = Field(default=0, ge=0)
    stall_days: int = Field(default=7, ge=1)

    # ---- Live note-taker (streaming STT) ----
    # Provider for real-time transcription of meeting audio. "none" disables it
    # (the WebSocket still runs but produces no transcript).
    stt_provider: Literal["deepgram", "none"] = "none"
    deepgram_api_key: SecretStr = SecretStr("")
    # Extraction cadence during a live meeting (see services/live_session.py).
    live_min_chars: int = Field(default=180, ge=20)
    live_min_interval_seconds: float = Field(default=8.0, ge=1.0)
    live_max_interval_seconds: float = Field(default=30.0, ge=5.0)

    @property
    def stt_enabled(self) -> bool:
        return self.stt_provider == "deepgram" and bool(
            self.deepgram_api_key.get_secret_value()
        )

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
        return bool(
            self.notion_api_key.get_secret_value() and self.notion_parent_page_id
        )

    @property
    def linear_enabled(self) -> bool:
        return bool(self.linear_api_key.get_secret_value() and self.linear_team_id)

    @property
    def jira_enabled(self) -> bool:
        return bool(
            self.jira_base_url
            and self.jira_api_token.get_secret_value()
            and self.jira_email
        )

    @property
    def slack_enabled(self) -> bool:
        return bool(
            self.slack_bot_token.get_secret_value() and self.slack_default_channel
        )

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
def get_settings() -> Settings:
    return Settings()
