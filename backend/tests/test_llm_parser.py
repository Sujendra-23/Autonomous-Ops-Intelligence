"""Tests for the resilient JSON parser inside the LLM client."""

from __future__ import annotations

import pytest

from app.llm.client import LLMError, _parse_extraction


VALID = """\
{
  "summary": "Brief summary",
  "project_hint": "Migration",
  "tasks": [
    {
      "title": "Finalize migration plan",
      "owner": "John",
      "due_date": "2026-05-30T00:00:00Z",
      "priority": "high",
      "source_quote": "John will own the migration plan by Friday.",
      "confidence": 0.92
    }
  ],
  "decisions": [],
  "risks": [],
  "blockers": []
}
"""


def test_parses_clean_json() -> None:
    result = _parse_extraction(VALID, provider="openai")
    assert result.project_hint == "Migration"
    assert len(result.tasks) == 1
    assert result.tasks[0].owner == "John"
    assert result.tasks[0].priority == "high"


def test_strips_code_fences() -> None:
    fenced = f"```json\n{VALID}\n```"
    result = _parse_extraction(fenced, provider="openai")
    assert result.summary == "Brief summary"


def test_recovers_when_model_adds_prose() -> None:
    polluted = "Sure! Here is the JSON:\n\n" + VALID + "\n\nLet me know if you need anything else."
    result = _parse_extraction(polluted, provider="anthropic")
    assert len(result.tasks) == 1


def test_invalid_json_raises_llm_error() -> None:
    with pytest.raises(LLMError):
        _parse_extraction("not json at all", provider="openai")


def test_schema_violation_raises_llm_error() -> None:
    bad = '{"summary": "ok"}'  # missing required fields downstream still fail at validation
    with pytest.raises(LLMError):
        # tasks/decisions etc default to []; summary is required and present.
        # Force a violation: confidence out of range.
        _parse_extraction(
            '{"summary": "ok", "tasks": [{"title":"x","source_quote":"q","confidence":5.0}]}',
            provider="openai",
        )
    _ = bad  # silence unused
