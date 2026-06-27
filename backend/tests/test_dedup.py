"""Unit tests for cross-meeting task de-duplication (no infrastructure)."""

from __future__ import annotations

from app.services.dedup import find_duplicate, normalize_title, title_similarity


def test_normalize_strips_punctuation_case_and_stopwords() -> None:
    assert normalize_title("  Update the Migration Script! ") == "update migration script"
    assert normalize_title("Fix THE bug, please.") == "fix bug"


def test_identical_after_normalisation_scores_one() -> None:
    assert title_similarity("Update migration script", "update the migration script") == 1.0


def test_containment_is_treated_as_high_similarity() -> None:
    # One title is a terser restatement of the other.
    assert title_similarity("migration script", "update the migration script") >= 0.9


def test_unrelated_titles_score_low() -> None:
    assert title_similarity("Write the launch blog post", "Provision prod database") < 0.5


def test_find_duplicate_returns_best_match_above_threshold() -> None:
    candidates = [
        (1, "Provision the prod database"),
        (2, "Finalize the migration plan"),
        (3, "Write release notes"),
    ]
    assert find_duplicate("finalize migration plan", candidates, threshold=0.82) == 2


def test_find_duplicate_returns_none_when_nothing_clears_threshold() -> None:
    candidates = [(1, "Provision the prod database"), (2, "Write release notes")]
    assert find_duplicate("Schedule the customer demo", candidates, threshold=0.82) is None


def test_empty_titles_never_match() -> None:
    assert title_similarity("", "anything") == 0.0
    assert find_duplicate("", [(1, "anything")], threshold=0.1) is None
