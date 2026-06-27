"""Embedding clients.

We support two providers:

- `openai` — production-quality embeddings via `text-embedding-3-small` (1536-d).
- `local`  — a deterministic hash-based fallback that lets the service boot
  and be tested without any API key. Quality is poor but stable, which is the
  right trade-off for CI and offline development.

Both project to the same dimensionality so the database column type doesn't
need to change between providers.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from functools import lru_cache

import openai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.logging import get_logger
from app.models.transcript import EMBEDDING_DIMS

logger = get_logger("app.llm.embeddings")


class EmbeddingClient(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAIEmbeddingClient(EmbeddingClient):
    def __init__(self) -> None:
        settings = get_settings()
        key = settings.openai_api_key.get_secret_value()
        if not key:
            raise RuntimeError("OPENAI_API_KEY required for OpenAI embeddings")
        self._client = openai.AsyncOpenAI(api_key=key)
        self._model = settings.openai_embedding_model

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError)
        ),
    )
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]


class LocalEmbeddingClient(EmbeddingClient):
    """Deterministic, dependency-free fallback.

    For each input we hash the text with SHA-256, expand the digest into a
    1536-d vector by stretching the hash bytes, then L2-normalise. Identical
    inputs yield identical vectors. It is *not* semantically meaningful — the
    purpose is to keep the pipeline alive offline.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _embed_one(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        # Stretch the 32-byte hash to fill 1536 floats using repeated hashing.
        buf = bytearray()
        h = seed
        while len(buf) < EMBEDDING_DIMS * 2:
            buf.extend(h)
            h = hashlib.sha256(h).digest()
        ints = [int.from_bytes(buf[i : i + 2], "big") for i in range(0, EMBEDDING_DIMS * 2, 2)]
        # Map to roughly [-1, 1] then normalise.
        raw = [(x / 32767.5) - 1.0 for x in ints]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


@lru_cache
def get_embedding_client() -> EmbeddingClient:
    settings = get_settings()
    has_openai = bool(settings.openai_api_key.get_secret_value())
    if settings.embedding_provider == "openai" and has_openai:
        return OpenAIEmbeddingClient()
    if settings.embedding_provider == "openai":
        logger.warning("embeddings.fallback_to_local", reason="OPENAI_API_KEY missing")
    return LocalEmbeddingClient()
