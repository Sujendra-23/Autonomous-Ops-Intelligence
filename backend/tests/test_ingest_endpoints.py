"""POST /api/transcripts/upload-document and /upload-recording, end to end.

The vision model is a stub; the page rendering, ffmpeg sampling, schema validation,
pipeline persistence and response model are the real code. Needs Postgres (skipped
without it, like the other pipeline tests).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api import transcripts as transcripts_api
from app.config import get_settings
from app.database import get_session
from app.integrations.dispatcher import IntegrationDispatcher
from app.llm.client import LLMClient, LLMError
from app.llm.embeddings import LocalEmbeddingClient
from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult

DOCS = Path(__file__).resolve().parents[1] / "evals" / "documents"
RESULT = ExtractionResult.model_validate(
    {
        "summary": "Platform sync",
        "project_hint": "Platform",
        "tasks": [
            {
                "title": "Rotate the staging database credentials",
                "owner": "Priya",
                "due_date": "2026-03-14T00:00:00Z",
                "source_quote": "Priya will rotate the staging database credentials",
                "confidence": 0.9,
            }
        ],
    }
)


class StubClient(LLMClient):
    def __init__(self) -> None:
        self.page_counts: list[int] = []
        self.captions: list[str] = []
        self.text_calls: list[str] = []

    async def extract(self, transcript_text: str, **kwargs: object) -> ExtractionResult:
        self.text_calls.append(transcript_text)
        return RESULT

    async def extract_images(self, images, **kwargs):
        self.page_counts.append(len(images))
        if kwargs.get("meeting_title") == "explode":
            raise LLMError("model unavailable")
        return RESULT

    async def caption_image(self, image, *, timestamp: str) -> str:
        self.captions.append(timestamp)
        return f"Board view at {timestamp}"


@pytest.fixture
async def http(db_session, monkeypatch):
    stub = StubClient()
    monkeypatch.setattr(transcripts_api, "get_llm_client", lambda: stub)
    monkeypatch.setattr("app.services.extraction.get_llm_client", lambda: stub)
    monkeypatch.setattr(
        "app.services.extraction.get_embedding_client", lambda: LocalEmbeddingClient()
    )

    class Quiet(IntegrationDispatcher):
        def __init__(self) -> None:
            super().__init__(project_mirrors=[], task_mirrors=[], notifiers=[])

    monkeypatch.setattr("app.services.extraction.IntegrationDispatcher", Quiet)
    app = FastAPI()
    app.include_router(transcripts_api.router, prefix="/api/transcripts")

    async def session():
        yield db_session

    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.stub = stub
        yield c


async def test_pdf_upload_returns_the_same_typed_detail_and_persists(http, db_session) -> None:
    pdf = (DOCS / "doc05.pdf").read_bytes()
    response = await http.post(
        "/api/transcripts/upload-document",
        files={"file": ("agenda.pdf", pdf, "application/pdf")},
        data={"title": "Platform sync"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["source"] == "document_upload"
    assert body["tasks"][0]["owner"] == "Priya"
    assert body["tasks"][0]["confidence"] == pytest.approx(0.9)
    assert http.stub.page_counts == [2]
    assert http.stub.text_calls == []  # the precomputed vision result skipped the text call
    rows = (await db_session.execute(select(Task))).scalars().all()
    assert [t.title for t in rows] == ["Rotate the staging database credentials"]
    stored = (await db_session.execute(select(Transcript))).scalars().one()
    assert "Priya will rotate" in stored.content


async def test_image_upload_works(http) -> None:
    response = await http.post(
        "/api/transcripts/upload-document",
        files={"file": ("scan.jpg", (DOCS / "doc04.jpg").read_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201
    assert http.stub.page_counts == [1]


@pytest.mark.parametrize(
    ("name", "data", "status", "fragment"),
    [
        ("notes.txt", b"hello", 422, "Unsupported file type"),
        ("bad.pdf", b"%PDF-1.4 nope", 422, "not a readable PDF"),
    ],
)
async def test_bad_documents_are_rejected(http, name, data, status, fragment) -> None:
    response = await http.post(
        "/api/transcripts/upload-document", files={"file": (name, data, "application/octet-stream")}
    )
    assert response.status_code == status
    assert fragment in response.json()["detail"]
    assert http.stub.page_counts == []


async def test_model_failure_is_a_502_and_nothing_is_persisted(http, db_session) -> None:
    response = await http.post(
        "/api/transcripts/upload-document",
        files={"file": ("a.pdf", (DOCS / "doc01.pdf").read_bytes(), "application/pdf")},
        data={"title": "explode"},
    )
    assert response.status_code == 502
    assert (await db_session.execute(select(Transcript))).first() is None


async def test_saas_mode_is_refused(http, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_mode", "oidc")
    response = await http.post(
        "/api/transcripts/upload-document",
        files={"file": ("a.pdf", (DOCS / "doc01.pdf").read_bytes(), "application/pdf")},
    )
    assert response.status_code == 501


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_recording_upload_merges_timestamped_captions_into_extraction(http, tmp_path) -> None:
    video = tmp_path / "demo.mp4"
    subprocess.run(  # noqa: S603
        [shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=160x120:r=5:d=2", "-f", "lavfi", "-i",
         "color=c=white:s=160x120:r=5:d=2", "-filter_complex", "[0:v][1:v]concat=n=2",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )  # fmt: skip
    response = await http.post(
        "/api/transcripts/upload-recording",
        files={"file": ("demo.mp4", video.read_bytes(), "video/mp4")},
        data={"title": "Demo"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "screen_recording"
    assert sorted(http.stub.captions) == ["00:00", "00:02"]
    merged = http.stub.text_calls[0]
    assert merged.startswith("## Screen captions\n[00:00] Board view at 00:00")
    assert "[00:02] Board view at 00:02" in merged
