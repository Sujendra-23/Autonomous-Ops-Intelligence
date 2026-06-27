"""Unit tests for the live note-taker's extraction-cadence logic (no infra)."""

from __future__ import annotations

from app.services.live_session import LiveSession


def test_no_extract_without_text() -> None:
    s = LiveSession()
    assert s.should_extract(now=100.0) is False


def test_first_extract_waits_for_min_chars() -> None:
    s = LiveSession(min_chars=50)
    s.add_final("short")  # < 50 chars
    assert s.should_extract(now=1.0) is False
    s.add_final("x" * 60)
    assert s.should_extract(now=1.0) is True


def test_min_interval_throttles_back_to_back_extractions() -> None:
    s = LiveSession(min_chars=10, min_interval_s=8.0, max_interval_s=30.0)
    s.add_final("x" * 20)
    assert s.should_extract(now=0.0) is True
    s.mark_extracted(now=0.0)

    s.add_final("y" * 20)
    # Only 3s later — below the min interval, so hold off.
    assert s.should_extract(now=3.0) is False
    # Past the min interval now.
    assert s.should_extract(now=9.0) is True


def test_max_interval_forces_extract_even_with_little_text() -> None:
    s = LiveSession(min_chars=1000, min_interval_s=8.0, max_interval_s=30.0)
    s.add_final("x" * 20)  # well below min_chars
    s.mark_extracted(now=0.0)
    s.add_final("y" * 20)
    assert s.should_extract(now=20.0) is False  # not enough text, not enough time
    assert s.should_extract(now=31.0) is True  # max interval elapsed


def test_mark_extracted_resets_pending_chars() -> None:
    s = LiveSession(min_chars=10)
    s.add_final("x" * 20)
    assert s.pending_chars == 20
    s.mark_extracted(now=5.0)
    assert s.pending_chars == 0
    assert s.should_extract(now=100.0) is False


def test_full_text_joins_finalized_segments_and_interim_is_separate() -> None:
    s = LiveSession()
    s.add_final("Hello team.")
    s.set_interim("we should")
    s.add_final("Let's ship Friday.")
    assert s.full_text() == "Hello team. Let's ship Friday."
    assert s.interim == ""  # cleared when a final arrives
