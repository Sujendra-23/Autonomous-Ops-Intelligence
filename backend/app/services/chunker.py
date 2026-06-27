"""Transcript chunking.

We chunk on paragraph boundaries with a target window size (in characters,
which approximates tokens well enough for non-CJK text) and a small overlap
so an utterance that crosses a chunk boundary doesn't get severed.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_TARGET_CHARS = 2400  # ~600 tokens for English transcripts
DEFAULT_OVERLAP_CHARS = 200


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str

    @property
    def token_estimate(self) -> int:
        # Rough heuristic: ~4 chars per token. Cheap and good enough for storage.
        return max(1, len(self.content) // 4)


def chunk_transcript(
    text: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Split `text` into overlapping chunks aligned to paragraph boundaries.

    Behaviour:
    - Empty/whitespace input returns an empty list.
    - If the transcript fits in one chunk, return one chunk.
    - Otherwise, accumulate paragraphs until we exceed `target_chars`, emit,
      then back up by `overlap_chars` worth of characters to start the next
      chunk so context is preserved across boundaries.
    """
    text = text.strip()
    if not text:
        return []

    if len(text) <= target_chars:
        return [Chunk(index=0, content=text)]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        # Single blob with no paragraph breaks — fall back to character windows.
        return _fixed_window(text, target_chars, overlap_chars)

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    idx = 0

    for para in paragraphs:
        para_len = len(para) + 2  # account for the join
        if buf_len + para_len > target_chars and buf:
            content = "\n\n".join(buf).strip()
            chunks.append(Chunk(index=idx, content=content))
            idx += 1
            # Carry forward `overlap_chars` worth of the previous chunk's tail.
            tail = content[-overlap_chars:] if overlap_chars else ""
            buf = [tail] if tail else []
            buf_len = len(tail)
        buf.append(para)
        buf_len += para_len

    if buf:
        content = "\n\n".join(buf).strip()
        if content:
            chunks.append(Chunk(index=idx, content=content))

    return chunks


def _fixed_window(text: str, target: int, overlap: int) -> list[Chunk]:
    step = max(1, target - overlap)
    chunks: list[Chunk] = []
    idx = 0
    for start in range(0, len(text), step):
        end = min(start + target, len(text))
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(index=idx, content=piece))
            idx += 1
        if end == len(text):
            break
    return chunks
