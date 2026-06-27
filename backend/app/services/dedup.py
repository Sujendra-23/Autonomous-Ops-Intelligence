"""Lightweight, dependency-free task de-duplication.

Cross-meeting extraction creates a fresh problem: the same task ("fix the
migration script") surfaces in standup after standup. We don't want a new row
every time. The LLM is told about existing open tasks and asked to emit updates
instead of duplicates (see ``services.context``), but models are fallible — so
the persistence layer keeps a deterministic safety net here.

Matching is intentionally *not* embedding-based: it must work with no OpenAI key
(the project ships a deterministic local embedding fallback that is non-semantic),
and it must be reproducible in tests. Normalised-title similarity via
``difflib`` is good enough to catch the obvious re-statements while staying
conservative — a high threshold means we'd rather create a near-duplicate than
silently swallow a genuinely new task.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TypeVar

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# Filler words that carry no signal for task identity. Stripping them stops
# "Update the migration script" and "Update migration script" from drifting apart.
_STOPWORDS = frozenset(
    {"the", "a", "an", "to", "for", "of", "and", "on", "in", "with", "please"}
)

K = TypeVar("K")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/stopwords, and collapse whitespace."""
    lowered = _PUNCT.sub(" ", title.lower())
    tokens = [t for t in _WS.sub(" ", lowered).split() if t and t not in _STOPWORDS]
    return " ".join(tokens)


def title_similarity(a: str, b: str) -> float:
    """Return a 0..1 similarity between two task titles after normalisation."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    # Containment is a strong signal one is a terser restatement of the other.
    if na in nb or nb in na:
        return max(ratio, 0.9)
    return ratio


def find_duplicate(
    title: str,
    candidates: list[tuple[K, str]],
    *,
    threshold: float,
) -> K | None:
    """Return the key of the best candidate whose title matches ``title``.

    ``candidates`` is a list of ``(key, candidate_title)``. Returns ``None`` when
    nothing clears ``threshold``.
    """
    best_key: K | None = None
    best_score = 0.0
    for key, candidate_title in candidates:
        score = title_similarity(title, candidate_title)
        if score > best_score:
            best_key, best_score = key, score
    return best_key if best_score >= threshold else None
