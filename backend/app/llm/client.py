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
from app.llm.prompts import (
    CAPTION_SYSTEM_PROMPT,
    DOCUMENT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_caption_prompt,
    build_document_prompt,
    build_user_prompt,
)
from app.llm.vision import ImagePart, anthropic_image_block, openai_image_block
from app.logging import get_logger
from app.metrics import track_llm_call
from app.schemas.extraction import ExtractionResult
from app.services.dlp import redact, redact_payload

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
    ) -> ExtractionResult: ...

    async def extract_images(
        self,
        images: list[ImagePart],
        *,
        meeting_title: str | None = None,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        project_hint: str | None = None,
    ) -> ExtractionResult:
        """Extract the same typed result from page images (scanned agenda, action list)."""
        raise LLMError(f"{type(self).__name__} does not support image input")

    async def caption_image(self, image: ImagePart, *, timestamp: str) -> str:
        """Describe one video keyframe in a sentence or two (operation ``caption_frame``)."""
        raise LLMError(f"{type(self).__name__} does not support image input")


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
        user_prompt = redact(user_prompt)
        with track_llm_call("openai", self._model, "extract") as call:
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

            usage = getattr(response, "usage", None)
            call.record_usage(
                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
            )
            raw = response.choices[0].message.content or ""
            try:
                return _parse_extraction(raw, provider="openai")
            except LLMError:
                call.outcome = "invalid_output"
                raise

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError)
        ),
    )
    async def extract_images(
        self,
        images: list[ImagePart],
        *,
        meeting_title: str | None = None,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        project_hint: str | None = None,
    ) -> ExtractionResult:
        prompt = build_document_prompt(
            len(images),
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            participants=participants,
            project_hint=project_hint,
        )
        content = [
            {"type": "text", "text": redact(prompt)},
            *(openai_image_block(i) for i in images),
        ]
        with track_llm_call("openai", self._model, "extract_document") as call:
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": DOCUMENT_SYSTEM_PROMPT},
                        {"role": "user", "content": content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
            except openai.OpenAIError as exc:
                raise LLMError(f"OpenAI call failed: {exc}") from exc

            usage = getattr(response, "usage", None)
            call.record_usage(
                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
            )
            raw = response.choices[0].message.content or ""
            try:
                return _parse_extraction(raw, provider="openai")
            except LLMError:
                call.outcome = "invalid_output"
                raise

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError)
        ),
    )
    async def caption_image(self, image: ImagePart, *, timestamp: str) -> str:
        content = [
            {"type": "text", "text": redact(build_caption_prompt(timestamp))},
            openai_image_block(image),
        ]
        with track_llm_call("openai", self._model, "caption_frame") as call:
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": CAPTION_SYSTEM_PROMPT},
                        {"role": "user", "content": content},
                    ],
                    max_tokens=300,
                    temperature=0.1,
                )
            except openai.OpenAIError as exc:
                raise LLMError(f"OpenAI call failed: {exc}") from exc
            usage = getattr(response, "usage", None)
            call.record_usage(
                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
            )
            return redact((response.choices[0].message.content or "").strip())


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
        user_prompt = redact(user_prompt)
        with track_llm_call("anthropic", self._model, "extract") as call:
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

            usage = getattr(response, "usage", None)
            call.record_usage(
                getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
            )
            raw = "".join(block.text for block in response.content if hasattr(block, "text"))
            try:
                return _parse_extraction(raw, provider="anthropic")
            except LLMError:
                call.outcome = "invalid_output"
                raise

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError)
        ),
    )
    async def extract_images(
        self,
        images: list[ImagePart],
        *,
        meeting_title: str | None = None,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        project_hint: str | None = None,
    ) -> ExtractionResult:
        prompt = build_document_prompt(
            len(images),
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            participants=participants,
            project_hint=project_hint,
        )
        content = [
            *(anthropic_image_block(i) for i in images),
            {"type": "text", "text": redact(prompt)},
        ]
        with track_llm_call("anthropic", self._model, "extract_document") as call:
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    system=DOCUMENT_SYSTEM_PROMPT,
                    max_tokens=4096,
                    temperature=0.1,
                    messages=[{"role": "user", "content": content}],
                )
            except anthropic.AnthropicError as exc:
                raise LLMError(f"Anthropic call failed: {exc}") from exc

            usage = getattr(response, "usage", None)
            call.record_usage(
                getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
            )
            raw = "".join(block.text for block in response.content if hasattr(block, "text"))
            try:
                return _parse_extraction(raw, provider="anthropic")
            except LLMError:
                call.outcome = "invalid_output"
                raise

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError)
        ),
    )
    async def caption_image(self, image: ImagePart, *, timestamp: str) -> str:
        content = [
            anthropic_image_block(image),
            {"type": "text", "text": redact(build_caption_prompt(timestamp))},
        ]
        with track_llm_call("anthropic", self._model, "caption_frame") as call:
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    system=CAPTION_SYSTEM_PROMPT,
                    max_tokens=300,
                    temperature=0.1,
                    messages=[{"role": "user", "content": content}],
                )
            except anthropic.AnthropicError as exc:
                raise LLMError(f"Anthropic call failed: {exc}") from exc
            usage = getattr(response, "usage", None)
            call.record_usage(
                getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
            )
            return redact("".join(b.text for b in response.content if hasattr(b, "text")).strip())


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
        logger.error("llm.parse_failed", provider=provider)
        raise LLMError(f"{provider} returned invalid JSON: {exc}") from exc

    try:
        return ExtractionResult.model_validate(redact_payload(data))
    except Exception as exc:
        logger.error("llm.schema_violation", provider=provider, error_type=type(exc).__name__)
        raise LLMError(f"{provider} output did not match schema: {exc}") from exc


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "openai":
        return OpenAIClient()
    if settings.llm_provider == "anthropic":
        return AnthropicClient()
    raise LLMError(f"Unknown LLM provider: {settings.llm_provider}")
