"""Audio/video transcription via OpenAI Whisper with ffmpeg pre-processing.

Flow for large files:
  1. Stream-save to disk (caller's responsibility — avoids 1 GB in RAM).
  2. ffmpeg re-encodes to 32 kbps mono mp3 (1 h ≈ 14 MB — well under Whisper's
     25 MB cap; even a 5-hour meeting compresses to ~70 MB).
  3. If the compressed file still exceeds WHISPER_CHUNK_BYTES (edge case for very
     long recordings), split into fixed-duration segments and transcribe each.
  4. Join chunk transcripts with a newline and return.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import openai

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("app.transcriber")

SUPPORTED_EXTENSIONS = {".mp4", ".mp3", ".m4a", ".wav", ".ogg", ".webm", ".flac"}
MAX_BYTES = 1 * 1024 * 1024 * 1024          # 1 GB — frontend enforces this too
WHISPER_CHUNK_BYTES = 20 * 1024 * 1024      # stay under Whisper's 25 MB hard limit
CHUNK_SECONDS = 4800                         # 80 min/chunk @ 32 kbps ≈ 19.2 MB


class TranscriptionError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# ffmpeg helpers (blocking — run in executor)                                 #
# --------------------------------------------------------------------------- #

def _run_ffmpeg(*args: str) -> None:
    try:
        result = subprocess.run(["ffmpeg", *args], capture_output=True)
    except FileNotFoundError:
        raise TranscriptionError(
            "ffmpeg is not available. Rebuild the backend image with `make build`."
        )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-600:]
        raise TranscriptionError(f"ffmpeg error: {stderr}")


def _compress(src: Path, dst: Path) -> None:
    """Re-encode any video/audio to 32 kbps mono mp3 at 16 kHz."""
    _run_ffmpeg(
        "-y", "-i", str(src),
        "-vn",              # drop video stream
        "-ar", "16000",     # 16 kHz sample rate (Whisper's native rate)
        "-ac", "1",         # mono
        "-b:a", "32k",      # 32 kbps — meeting speech is perfectly clear
        str(dst),
    )


def _split(src: Path, chunk_dir: Path) -> list[Path]:
    """Split mp3 into CHUNK_SECONDS-long segments. Returns sorted chunk paths."""
    pattern = str(chunk_dir / "chunk_%03d.mp3")
    _run_ffmpeg(
        "-y", "-i", str(src),
        "-f", "segment",
        "-segment_time", str(CHUNK_SECONDS),
        "-c", "copy",
        pattern,
    )
    return sorted(chunk_dir.glob("chunk_*.mp3"))


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

async def transcribe_path(src: Path, filename: str) -> str:
    """Transcribe an already-saved file. Handles compression and chunking."""
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise TranscriptionError(
            "OPENAI_API_KEY is required for transcription — add it to .env."
        )

    ext = src.suffix.lower() or Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise TranscriptionError(
            f"Unsupported format '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    size_mb = src.stat().st_size / 1024 / 1024
    logger.info("transcriber.start", filename=filename, mb=round(size_mb, 1))

    client = openai.AsyncOpenAI(api_key=key)
    loop = asyncio.get_event_loop()

    # Work in a sibling temp directory so we never touch the caller's file
    work_dir = src.parent / "work"
    work_dir.mkdir(exist_ok=True)

    try:
        compressed = work_dir / "audio.mp3"
        await loop.run_in_executor(None, _compress, src, compressed)
        logger.info(
            "transcriber.compressed",
            original_mb=round(size_mb, 1),
            compressed_mb=round(compressed.stat().st_size / 1024 / 1024, 1),
        )

        if compressed.stat().st_size > WHISPER_CHUNK_BYTES:
            chunk_dir = work_dir / "chunks"
            chunk_dir.mkdir()
            chunks = await loop.run_in_executor(None, _split, compressed, chunk_dir)
        else:
            chunks = [compressed]

        logger.info("transcriber.chunks", count=len(chunks))

        parts: list[str] = []
        for i, chunk in enumerate(chunks):
            with chunk.open("rb") as fh:
                response = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=(chunk.name, fh),
                    response_format="text",
                )
            text = response if isinstance(response, str) else str(response)
            parts.append(text.strip())
            logger.info("transcriber.chunk_done", index=i + 1, total=len(chunks))

    finally:
        # Clean up intermediate files; the original src is the caller's to remove
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)

    result = "\n".join(parts)
    logger.info("transcriber.done", chars=len(result))
    return result
