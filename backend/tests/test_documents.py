"""Document ingestion: page rendering, vision adapters, metrics, and the eval scorer."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from prometheus_client import REGISTRY

from app.llm.client import AnthropicClient, LLMClient, LLMError, OpenAIClient
from app.llm.vision import ImagePart
from app.schemas.extraction import ExtractionResult
from app.services.documents import (
    DocumentError,
    extract_document,
    prepare_pages,
    result_to_text,
)
from evals import run_documents

DOCS = Path(__file__).resolve().parents[1] / "evals" / "documents"
MANIFEST = json.loads((DOCS.parent / "documents.json").read_text())["cases"]

VALID = json.dumps(
    {
        "summary": "Agenda review",
        "tasks": [
            {
                "title": "Rotate staging credentials",
                "owner": "Priya",
                "due_date": "2026-03-14T00:00:00Z",
                "source_quote": "Priya will rotate staging credentials by 2026-03-14.",
                "confidence": 0.93,
            }
        ],
    }
)


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #


def test_every_eval_document_renders_to_the_expected_page_count() -> None:
    assert len(MANIFEST) == 20
    for case in MANIFEST:
        pages = prepare_pages(case["file"], (DOCS / case["file"]).read_bytes())
        assert len(pages) == case["pages"], case["id"]
        assert all(p.media_type == "image/png" and p.data[:4] == b"\x89PNG" for p in pages)


def test_large_images_are_downscaled() -> None:
    (page,) = prepare_pages("big.png", png_bytes((4000, 3000)))
    assert max(Image.open(io.BytesIO(page.data)).size) == 1568


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("notes.docx", b"x", "Unsupported file type"),
        ("empty.pdf", b"", "empty"),
        ("broken.pdf", b"%PDF-1.4 not really", "not a readable PDF"),
        ("broken.png", b"\x89PNG nope", "not a readable image"),
    ],
)
def test_bad_uploads_raise_document_error(name: str, data: bytes, message: str) -> None:
    with pytest.raises(DocumentError, match=message):
        prepare_pages(name, data)


def test_page_cap_rejects_long_pdfs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.documents.MAX_PAGES", 1)
    with pytest.raises(DocumentError, match="2 pages"):
        prepare_pages("doc05.pdf", (DOCS / "doc05.pdf").read_bytes())


def test_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.documents.MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(DocumentError, match="limit"):
        prepare_pages("a.png", png_bytes())


def test_rendering_counts_pages_by_file_type() -> None:
    before = sample("aoi_ingest_document_pages_total", file_type="pdf")
    prepare_pages("doc05.pdf", (DOCS / "doc05.pdf").read_bytes())
    assert sample("aoi_ingest_document_pages_total", file_type="pdf") == before + 2


# --------------------------------------------------------------------------- #
# Provider adapters                                                           #
# --------------------------------------------------------------------------- #


def make_anthropic(create) -> AnthropicClient:
    client = AnthropicClient.__new__(AnthropicClient)
    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    client._model = "test-model-a"
    return client


def make_openai(create) -> OpenAIClient:
    client = OpenAIClient.__new__(OpenAIClient)
    completions = SimpleNamespace(create=create)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client._model = "test-model-o"
    return client


async def test_anthropic_sends_image_blocks_and_returns_the_typed_result() -> None:
    seen: dict = {}

    async def create(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text=VALID)],
            usage=SimpleNamespace(input_tokens=900, output_tokens=60),
        )

    labels = {"provider": "anthropic", "model": "test-model-a", "operation": "extract_document"}
    calls = sample("aoi_llm_requests_total", **labels, outcome="success")
    pages = [ImagePart(png_bytes()), ImagePart(png_bytes())]

    result = await make_anthropic(create).extract_images(pages, meeting_date="2026-03-02")

    assert isinstance(result, ExtractionResult)
    assert result.tasks[0].owner == "Priya"
    blocks = seen["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image", "image", "text"]
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert "Meeting date: 2026-03-02" in blocks[2]["text"]
    assert sample("aoi_llm_requests_total", **labels, outcome="success") == calls + 1
    assert sample("aoi_llm_tokens_total", **labels, direction="input") >= 900


async def test_openai_sends_data_urls_and_returns_the_typed_result() -> None:
    seen: dict = {}

    async def create(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        message = SimpleNamespace(content=VALID)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=800, completion_tokens=50),
        )

    result = await make_openai(create).extract_images([ImagePart(png_bytes())])

    assert result.tasks[0].title == "Rotate staging credentials"
    parts = seen["messages"][1]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_invalid_model_output_is_counted_and_raises() -> None:
    async def create(**_: object) -> SimpleNamespace:
        return SimpleNamespace(content=[SimpleNamespace(text='{"tasks": 5}')], usage=None)

    labels = {
        "provider": "anthropic",
        "model": "test-model-a",
        "operation": "extract_document",
        "outcome": "invalid_output",
    }
    before = sample("aoi_llm_requests_total", **labels)
    with pytest.raises(LLMError):
        await make_anthropic(create).extract_images([ImagePart(png_bytes())])
    assert sample("aoi_llm_requests_total", **labels) == before + 1


async def test_clients_without_vision_support_raise_llm_error() -> None:
    class TextOnly(LLMClient):
        async def extract(self, *a: object, **k: object) -> ExtractionResult:  # pragma: no cover
            raise AssertionError

    with pytest.raises(LLMError, match="does not support image input"):
        await TextOnly().extract_images([ImagePart(png_bytes())])


async def test_extract_document_renders_then_calls_the_client() -> None:
    received: list[list[ImagePart]] = []

    class Recorder(LLMClient):
        async def extract(self, *a: object, **k: object) -> ExtractionResult:  # pragma: no cover
            raise AssertionError

        async def extract_images(self, images: list[ImagePart], **kwargs: object):
            received.append(images)
            return ExtractionResult.model_validate_json(VALID)

    result = await extract_document(Recorder(), "doc05.pdf", (DOCS / "doc05.pdf").read_bytes())
    assert len(received[0]) == 2
    assert "Priya will rotate" in result_to_text(result)


# --------------------------------------------------------------------------- #
# Eval scorer                                                                 #
# --------------------------------------------------------------------------- #

CASE = {
    "id": "t",
    "text": ["Priya will rotate the credentials by 2026-03-14", "Adopt weekly releases"],
    "tasks": [
        {"title": "Rotate the credentials", "owner": "Priya", "due_date": "2026-03-14"},
        {"title": "Write the FAQ", "owner": None, "due_date": "2026-03-20"},
        {"title": "Review the vendor form", "owner": "Marcus", "due_date": "2026-03-21"},
    ],
    "decisions": [{"summary": "Adopt weekly releases"}],
    "risks": [{"title": "Vendor may slip"}],
}


def test_scorer_hand_computed_precision_and_recall() -> None:
    predicted = {
        "tasks": [
            # matched, owner right, date right
            {
                "title": "Rotate the credentials",
                "owner": "priya",
                "due_date": "2026-03-14T00:00:00Z",
                "source_quote": "Priya will rotate the credentials",
            },
            # matched, owner invented where reference has none, date wrong
            {
                "title": "Write the FAQ",
                "owner": "Elena",
                "due_date": "2026-03-25T00:00:00Z",
                "source_quote": "not in the document",
            },
            # extra task with no counterpart
            {
                "title": "Plan the offsite",
                "owner": "Kwame",
                "due_date": None,
                "source_quote": "Adopt weekly releases",
            },
        ],
        "decisions": [
            {"summary": "Adopt weekly releases", "source_quote": "Adopt weekly releases"}
        ],
        "risks": [],
    }
    s = run_documents.score(CASE, predicted)
    f = s.fields
    assert (f["tasks"].tp, f["tasks"].fp, f["tasks"].fn) == (2, 1, 1)
    # owner: Priya TP; Elena FP (reference none); Kwame FP (unmatched task); Marcus FN (missed task)
    assert (f["task.owner"].tp, f["task.owner"].fp, f["task.owner"].fn) == (1, 2, 1)
    # due_date: TP 1; wrong date is FP+FN; the missed task's date is another FN; Kwame has none
    assert (f["task.due_date"].tp, f["task.due_date"].fp, f["task.due_date"].fn) == (1, 1, 2)
    assert (f["decisions"].tp, f["decisions"].fp, f["decisions"].fn) == (1, 0, 0)
    assert (f["risks"].tp, f["risks"].fp, f["risks"].fn) == (0, 0, 1)
    assert f["tasks"].precision == pytest.approx(2 / 3)
    assert f["tasks"].recall == pytest.approx(2 / 3)
    assert (s.quotes_grounded, s.quotes_total) == (3, 4)


def test_scorer_perfect_and_empty_cases() -> None:
    empty = {**CASE, "tasks": [], "decisions": [], "risks": []}
    s = run_documents.score(empty, {"tasks": [], "decisions": [], "risks": []})
    assert s.fields["tasks"].precision is None and s.fields["tasks"].recall is None


async def test_offline_eval_runs_every_document_and_passes(capsys: pytest.CaptureFixture) -> None:
    assert await run_documents.run(live=False) == 0
    out = capsys.readouterr().out
    assert "20 documents, 0 failed (offline-stub)" in out
    assert "validates the harness only" in out


async def test_live_eval_refuses_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert await run_documents.run(live=True) == 2
    finally:
        get_settings.cache_clear()
    assert "none configured" in capsys.readouterr().out
