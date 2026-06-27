"""In-memory state for one live meeting: transcript buffer + extraction cadence.

This holds no database or network handles on purpose — it is pure, deterministic
logic so the (slightly fiddly) "when should we re-extract?" decision can be
unit-tested without a live socket. The live API owns one of these per WebSocket.

Re-extracting on *every* finalized phrase would be slow and expensive; never
re-extracting means notes lag the meeting. So we debounce: extract once enough
new text has accumulated, but not more often than ``min_interval_s``, and at
least every ``max_interval_s`` so a slow stretch of conversation still refreshes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LiveSession:
    min_chars: int = 180
    min_interval_s: float = 8.0
    max_interval_s: float = 30.0

    _segments: list[str] = field(default_factory=list)
    _chars_since_extract: int = 0
    _last_extract_at: float | None = None
    interim: str = ""

    def seed(self, text: str) -> None:
        """Pre-load already-transcribed text (e.g. on WebSocket reconnect).

        Counts as nothing pending, so it doesn't immediately trigger a
        re-extraction — it just keeps ``full_text()`` continuous so a reconnect
        doesn't overwrite the transcript with only the post-reconnect tail.
        """
        text = text.strip()
        if text:
            self._segments = [text]
            self._chars_since_extract = 0

    def add_final(self, text: str) -> None:
        """Record a finalized transcript segment."""
        text = text.strip()
        if not text:
            return
        self._segments.append(text)
        self._chars_since_extract += len(text)
        self.interim = ""

    def set_interim(self, text: str) -> None:
        """Store the latest non-final hypothesis (for live display only)."""
        self.interim = text.strip()

    def full_text(self) -> str:
        return " ".join(self._segments).strip()

    @property
    def pending_chars(self) -> int:
        return self._chars_since_extract

    def should_extract(self, now: float) -> bool:
        """Whether enough has changed to warrant another extraction pass."""
        if self._chars_since_extract == 0:
            return False
        if self._last_extract_at is None:
            # First pass: wait until there's a meaningful amount of text.
            return self._chars_since_extract >= self.min_chars
        elapsed = now - self._last_extract_at
        if elapsed < self.min_interval_s:
            return False
        return self._chars_since_extract >= self.min_chars or elapsed >= self.max_interval_s

    def mark_extracted(self, now: float) -> None:
        self._chars_since_extract = 0
        self._last_extract_at = now
