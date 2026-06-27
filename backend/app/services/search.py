"""Semantic search over transcript chunks using pgvector."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.embeddings import get_embedding_client
from app.models.transcript import Transcript, TranscriptChunk


@dataclass
class SearchHit:
    chunk_id: str
    transcript_id: str
    transcript_title: str
    content: str
    score: float


async def semantic_search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 5,
) -> list[SearchHit]:
    query = query.strip()
    if not query:
        return []

    embedder = get_embedding_client()
    [vec] = await embedder.embed([query])

    # Cosine distance — lower is better. We expose a similarity-like score.
    distance = TranscriptChunk.embedding.cosine_distance(vec).label("distance")
    stmt = (
        select(
            TranscriptChunk.id,
            TranscriptChunk.transcript_id,
            TranscriptChunk.content,
            Transcript.title,
            distance,
        )
        .join(Transcript, Transcript.id == TranscriptChunk.transcript_id)
        .where(TranscriptChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        SearchHit(
            chunk_id=str(row.id),
            transcript_id=str(row.transcript_id),
            transcript_title=row.title,
            content=row.content,
            score=float(1.0 - row.distance),
        )
        for row in rows
    ]
