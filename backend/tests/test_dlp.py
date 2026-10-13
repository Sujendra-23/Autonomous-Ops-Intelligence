"""Label policy, checksum/overlap regression, persistence and real send boundaries."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.integrations.jira import JiraAdapter, _p
from app.integrations.slack import SlackAdapter
from app.llm.client import AnthropicClient, OpenAIClient
from app.llm.embeddings import OpenAIEmbeddingClient
from app.llm.vision import ImagePart
from app.models.transcript import Transcript, TranscriptChunk
from app.schemas.extraction import ExtractionResult
from app.services.dlp import classify, detect, protect_transcript, redact, redact_payload
from app.services.extraction import ExtractionPipeline

CASES = json.loads((Path(__file__).parents[1] / "evals/dlp.json").read_text())
SECRET = "alice@example.com"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_labeling_eval(case):
    result = classify(case["text"], case["requested_label"])
    assert result.label == case["expected_label"]
    assert result.counts == case["expected_counts"]
    assert not detect(result.text)
    assert redact(result.text) == result.text


def test_transcript_labels_and_findings_survive_retries_and_live_growth():
    transcript = Transcript(
        title=SECRET,
        content="Routine discussion.",
        participants=[SECRET],
        sensitivity_label="public",
    )
    protect_transcript(transcript)
    assert transcript.sensitivity_label == "confidential"
    assert SECRET not in transcript.title
    before = dict(transcript.dlp_findings)
    protect_transcript(transcript)
    assert transcript.dlp_findings == before
    transcript.content += " Call 415-555-2671."
    protect_transcript(transcript)
    assert transcript.sensitivity_label == "confidential"
    assert transcript.dlp_findings["PHONE"] == 1
    assert "415-555-2671" not in transcript.content


def test_payload_redaction_preserves_machine_ids_and_does_not_mutate():
    identifier = str(uuid.uuid4())
    original = {"id": identifier, "nested": [{"owner": SECRET}], "count": 3}
    result = redact_payload(original)
    assert result == {"id": identifier, "nested": [{"owner": "[REDACTED:EMAIL]"}], "count": 3}
    assert original["nested"][0]["owner"] == SECRET


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("method", ["extract", "extract_images"])
async def test_llm_redacts_every_prompt_field_and_provider_output(provider, method):
    output = json.dumps({"summary": SECRET})
    create = AsyncMock(
        return_value=(
            SimpleNamespace(content=[SimpleNamespace(text=output)], usage=None)
            if provider == "anthropic"
            else SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=output))], usage=None
            )
        )
    )
    klass = AnthropicClient if provider == "anthropic" else OpenAIClient
    client = klass.__new__(klass)
    client._model = "test"
    client._client = (
        SimpleNamespace(messages=SimpleNamespace(create=create))
        if provider == "anthropic"
        else SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )
    metadata = {"meeting_title": SECRET, "participants": [SECRET], "project_hint": SECRET}
    if method == "extract":
        result = await client.extract(SECRET, prior_context=SECRET, **metadata)
    else:
        result = await client.extract_images([ImagePart(b"synthetic-image")], **metadata)
    sent = json.dumps(create.call_args.kwargs)
    assert SECRET not in sent
    assert "[REDACTED:EMAIL]" in sent
    assert result.summary == "[REDACTED:EMAIL]"


def test_jira_redacts_before_truncation_splits_a_match():
    paragraph = _p(" " * 1995 + SECRET)
    assert paragraph["content"][0]["text"] == (" " * 1995 + "[REDACTED:EMAIL]")[:2000]
    assert "alice" not in paragraph["content"][0]["text"]


async def test_embedding_boundary_redacts_legacy_input():
    create = AsyncMock(return_value=SimpleNamespace(data=[]))
    client = OpenAIEmbeddingClient.__new__(OpenAIEmbeddingClient)
    client._model = "test"
    client._client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    await client.embed([SECRET])
    assert create.call_args.kwargs["input"] == ["[REDACTED:EMAIL]"]


async def test_slack_and_jira_redact_at_http_boundary():
    sent = []

    async def capture(url, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    http = AsyncMock()
    http.post.side_effect = capture
    context = AsyncMock()
    context.__aenter__.return_value = http
    with (
        patch("httpx.AsyncClient", return_value=context),
        patch("app.integrations.slack.access_token", AsyncMock(return_value="test"), create=True),
        patch(
            "app.integrations.jira.jira_connection",
            AsyncMock(return_value=("https://example.atlassian.net", {}, None)),
            create=True,
        ),
    ):
        slack = SlackAdapter()
        await slack._post_text(SECRET)
        await slack._post_blocks([{"text": {"text": SECRET}}], fallback=SECRET)
        await JiraAdapter()._post("/rest/api/3/issue", {"fields": {"summary": SECRET}})
    assert len(sent) == 3
    assert SECRET not in json.dumps(sent)


async def test_async_ingest_is_classified_before_pipeline_and_cannot_downgrade(db_session):
    transcript = Transcript(
        title="Synthetic meeting",
        content=f"Contact {SECRET} about release.",
        sensitivity_label="public",
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)
    assert transcript.sensitivity_label == "confidential"
    assert SECRET not in transcript.content
    transcript.sensitivity_label = "public"
    await db_session.commit()
    await db_session.refresh(transcript)
    assert transcript.sensitivity_label == "confidential"


async def test_full_and_incremental_pipeline_redact_inputs_and_outputs(db_session):
    transcript = Transcript(title=SECRET, content=f"Contact {SECRET} about the release today.")
    db_session.add(transcript)
    await db_session.commit()
    dispatcher = SimpleNamespace(publish=AsyncMock())
    llm = SimpleNamespace(extract=AsyncMock(return_value=ExtractionResult(summary=SECRET)))
    embedder = SimpleNamespace(embed=AsyncMock(side_effect=lambda texts: [None] * len(texts)))
    with (
        patch("app.services.extraction.get_llm_client", return_value=llm),
        patch("app.services.extraction.get_embedding_client", return_value=embedder),
    ):
        pipeline = ExtractionPipeline(db_session, dispatcher=dispatcher)
        await pipeline.extract_incremental(transcript.id)
        result = await pipeline.process(transcript.id)
    assert result.summary == "[REDACTED:EMAIL]"
    assert SECRET not in str(llm.extract.call_args_list)
    assert SECRET not in str(embedder.embed.call_args_list)
    chunks = (await db_session.scalars(select(TranscriptChunk))).all()
    assert chunks and all(SECRET not in chunk.content for chunk in chunks)
    assert SECRET not in str(dispatcher.publish.call_args)
