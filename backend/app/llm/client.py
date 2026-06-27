"""LLM client supporting OpenAI and Anthropic with a single interface.

We do not stream — extraction is a one-shot structured-output call and the
downstream pipeline needs a complete object before persisting anything.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from functools import lru_cache

import anthropic
import openai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.logging import get_logger
from app.schemas.extraction import ExtractionResult

logger = get_logger("app.llm")


class LLMError(RuntimeError):
    """Wraps any provider-side failure so callers handle one exception type."""


class LLMClient(ABC):
    @abstractmethod
    async def extract(
        self,
        transcript_text: str,
        *,
        meeting_title: str | None = None,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        project_hint: str | None = None,
        prior_context: str | None = None,
    ) -> ExtractionResult:
        ...


# --------------------------------------------------------------------------- #
# OpenAI                                                                      #
# --------------------------------------------------------------------------- #


class OpenAIClient(LLMClient):
    def __init__(self) -> None:
        settings = get_settings()
        key = settings.openai_api_key.get_secret_value()
        if not key:
            raise LLMError("OPENAI_API_KEY is not configured")
        self._client = openai.AsyncOpenAI(api_key=key)
        self._model = settings.openai_model

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError)
        ),
    )
    async def extract(
        self,
        transcript_text: str,
        *,
        meeting_title: str | None = None,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        project_hint: str | None = None,
        prior_context: str | None = None,
    ) -> ExtractionResult:
        user_prompt = build_user_prompt(
            transcript_text,
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            participants=participants,
            project_hint=project_hint,
            prior_context=prior_context,
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc

        raw = response.choices[0].message.content or ""
        return _parse_extraction(raw, provider="openai")


# --------------------------------------------------------------------------- #
# Anthropic                                                                   #
# --------------------------------------------------------------------------- #


class AnthropicClient(LLMClient):
    def __init__(self) -> None:
        settings = get_settings()
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not configured")
        self._client = anthropic.AsyncAnthropic(api_key=key)
        self._model = settings.anthropic_model

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError)
        ),
    )
    async def extract(
        self,
        transcript_text: str,
        *,
        meeting_title: str | None = None,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        project_hint: str | None = None,
        prior_context: str | None = None,
    ) -> ExtractionResult:
        user_prompt = build_user_prompt(
            transcript_text,
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            participants=participants,
            project_hint=project_hint,
            prior_context=prior_context,
        )
        try:
            response = await self._client.messages.create(
                model=self._model,
                system=SYSTEM_PROMPT,
                max_tokens=4096,
                temperature=0.1,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Anthropic call failed: {exc}") from exc

        raw = "".join(block.text for block in response.content if hasattr(block, "text"))
        return _parse_extraction(raw, provider="anthropic")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _parse_extraction(raw: str, *, provider: str) -> ExtractionResult:
    """Tolerate models that wrap JSON in code fences or trailing prose."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)

    # If the model wrote anything before/after the JSON, slice out the object.
    if not cleaned.startswith("{"):
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            cleaned = cleaned[first : last + 1]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("llm.parse_failed", provider=provider, raw=raw[:500])
        raise LLMError(f"{provider} returned invalid JSON: {exc}") from exc

    try:
        return ExtractionResult.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — pydantic raises various subclasses
        logger.error(
            "llm.schema_violation", provider=provider, data=data, error=str(exc)
        )
        raise LLMError(f"{provider} output did not match schema: {exc}") from exc


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "openai":
        return OpenAIClient()
    if settings.llm_provider == "anthropic":
        return AnthropicClient()
    raise LLMError(f"Unknown LLM provider: {settings.llm_provider}")
