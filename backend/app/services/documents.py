"""Scanned document → typed extraction (PDF or image pages sent to a vision model).

Flow:

  1. Validate the upload (size, type) and render PDF pages to PNG with pdfium,
     or normalise a single PNG/JPEG/WebP image.
  2. Send the page images to the configured provider through ``LLMClient.extract_images``
     (Claude by default, OpenAI supported).
  3. The reply is validated against the same ``ExtractionResult`` model the
     transcript path uses, so everything downstream is unchanged.

Nothing is OCR'd locally; pages are capped so a hostile PDF cannot fan out into
hundreds of model calls or gigapixel renders.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from app.llm.client import LLMClient, LLMError
from app.llm.vision import ImagePart
from app.metrics import DOCUMENT_PAGES
from app.schemas.extraction import ExtractionResult

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_PAGES = 10
MAX_EDGE_PX = 1568  # larger edges are downscaled by the providers anyway
RENDER_DPI = 150
MAX_SOURCE_PIXELS = 40_000_000  # reject decompression bombs before decoding

Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS


class DocumentError(ValueError):
    """The upload cannot be read as a document; safe to show to the caller."""


def _to_png(image: Image.Image) -> bytes:
    image = image.convert("RGB")
    if max(image.size) > MAX_EDGE_PX:
        image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_pdf_pages(data: bytes, *, max_pages: int | None = None) -> list[ImagePart]:
    max_pages = MAX_PAGES if max_pages is None else max_pages
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:  # pdfium raises PdfiumError or ValueError
        raise DocumentError("File is not a readable PDF") from exc
    try:
        total = len(pdf)
        if total == 0:
            raise DocumentError("PDF has no pages")
        if total > max_pages:
            raise DocumentError(f"PDF has {total} pages; the limit is {max_pages}")
        pages: list[ImagePart] = []
        for index in range(total):
            page = pdf[index]
            try:
                width, height = page.get_size()
                scale = RENDER_DPI / 72
                if width * scale * height * scale > MAX_SOURCE_PIXELS:
                    raise DocumentError(f"Page {index + 1} is too large to render")
                bitmap = page.render(scale=scale)
                pages.append(
                    ImagePart(_to_png(bitmap.to_pil()), "image/png", label=f"page {index + 1}")
                )
            finally:
                page.close()
        return pages
    finally:
        pdf.close()


def load_image(data: bytes) -> list[ImagePart]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return [ImagePart(_to_png(image), "image/png", label="page 1")]
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DocumentError("File is not a readable image") from exc


def prepare_pages(filename: str, data: bytes) -> list[ImagePart]:
    """Turn an uploaded PDF or image into model-ready page images."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentError(
            f"Unsupported file type {ext or '(none)'}; use one of "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )
    if not data:
        raise DocumentError("File is empty")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(f"File exceeds the {MAX_DOCUMENT_BYTES // 1024 // 1024} MB limit")
    pages = render_pdf_pages(data) if ext == ".pdf" else load_image(data)
    DOCUMENT_PAGES.labels(ext.lstrip(".")).inc(len(pages))
    return pages


async def extract_document(
    client: LLMClient,
    filename: str,
    data: bytes,
    *,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    participants: Sequence[str] | None = None,
    project_hint: str | None = None,
) -> ExtractionResult:
    """Render ``data`` and run vision extraction. Raises ``DocumentError`` or ``LLMError``."""
    pages = prepare_pages(filename, data)
    return await client.extract_images(
        pages,
        meeting_title=meeting_title,
        meeting_date=meeting_date,
        participants=list(participants) if participants else None,
        project_hint=project_hint,
    )


def result_to_text(result: ExtractionResult) -> str:
    """Searchable stand-in for transcript content: summary plus the verbatim quotes."""
    lines = [result.summary.strip()]
    for group in (
        result.tasks,
        result.decisions,
        result.risks,
        result.blockers,
        result.task_updates,
    ):
        lines.extend(item.source_quote.strip() for item in group)
    return "\n".join(line for line in lines if line)


__all__ = [
    "DocumentError",
    "LLMError",
    "extract_document",
    "load_image",
    "prepare_pages",
    "render_pdf_pages",
    "result_to_text",
]
