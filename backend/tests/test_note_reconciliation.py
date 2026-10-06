"""Regression checks for notes repeated by live extraction passes."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.risk import Risk
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult
from app.services.extraction import ExtractionPipeline


@pytest.mark.asyncio
async def test_repeated_live_passes_update_notes_and_preserve_other_meetings():
    rows = []
    session = MagicMock()
    session.flush = AsyncMock()
    session.add.side_effect = rows.append

    async def execute(query):
        model = query.column_descriptions[0]["entity"]
        transcript_id = query.compile().params["transcript_id_1"]
        result = MagicMock()
        result.scalars.return_value.all.return_value = [
            row for row in rows if isinstance(row, model) and row.transcript_id == transcript_id
        ]
        return result

    session.execute = AsyncMock(side_effect=execute)
    pipeline = ExtractionPipeline(session)
    transcript = Transcript(id=uuid.uuid4(), content="Meeting")
    payload = dict(
        summary="Meeting",
        decisions=[
            dict(summary="Use staged rollout", source_quote="Staged rollout", confidence=0.9)
        ],
        risks=[dict(title="Launch delay", source_quote="Delay", confidence=0.8)],
        blockers=[
            dict(summary="Event support is uncertain", source_quote="Support", confidence=0.8)
        ],
    )
    first = ExtractionResult(**payload)
    # A single response can itself contain the same blocker twice.
    first.blockers.append(first.blockers[0].model_copy())
    await pipeline._persist_artefacts(transcript, None, first)
    assert len(rows) == 3
    blocker = next(row for row in rows if isinstance(row, Blocker))
    blocker.status = "resolved"
    blocker.extra_metadata = {"manual_note": "Retain"}
    original_rows = tuple(rows)

    second = ExtractionResult(**payload)
    second.blockers[0].summary = "  EVENT support is   uncertain "
    second.blockers[0].needs_from = "Events team"
    second.blockers[0].severity = "high"
    await pipeline._persist_artefacts(transcript, None, second)
    await pipeline._persist_artefacts(transcript, None, first)
    assert tuple(rows) == original_rows
    assert blocker.needs_from == "Events team"
    assert blocker.status == "resolved"
    assert blocker.extra_metadata == {"manual_note": "Retain"}
    assert {type(row) for row in rows} == {Decision, Risk, Blocker}

    # Similar wording with different meaning must remain separate.
    different = ExtractionResult(**payload)
    different.blockers[0].summary = "Venue support is uncertain"
    await pipeline._persist_artefacts(transcript, None, different)
    assert len(rows) == 4

    other = Transcript(id=uuid.uuid4(), content="Another meeting")
    await pipeline._persist_artefacts(other, None, ExtractionResult(**payload))
    assert len(rows) == 7
