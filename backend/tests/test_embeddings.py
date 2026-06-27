import math

import pytest

from app.llm.embeddings import LocalEmbeddingClient
from app.models.transcript import EMBEDDING_DIMS


@pytest.mark.asyncio
async def test_local_embedding_is_deterministic_and_normalised() -> None:
    client = LocalEmbeddingClient()
    [a, b] = await client.embed(["hello world", "hello world"])
    assert a == b
    norm = math.sqrt(sum(x * x for x in a))
    assert 0.99 <= norm <= 1.01
    assert len(a) == EMBEDDING_DIMS


@pytest.mark.asyncio
async def test_local_embedding_differs_for_different_input() -> None:
    client = LocalEmbeddingClient()
    [a, b] = await client.embed(["foo", "bar"])
    assert a != b
