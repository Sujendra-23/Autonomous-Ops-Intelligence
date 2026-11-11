"""Evaluate live ASR against an explicit synthetic/reference fixture (billable API call)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
from app.config import get_settings  # noqa: E402
from app.services.live_stt import OpenAIRealtimeTranscriber  # noqa: E402


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word edits / reference words, ignoring punctuation/case, not numeric formatting."""

    def words(text):
        return re.findall(r"\w+", text.casefold())

    expected, actual = words(reference), words(hypothesis)
    if not expected:
        raise ValueError("Reference text must contain words")
    previous = list(range(len(actual) + 1))
    for index, word in enumerate(expected, start=1):
        current = [index]
        for j, other in enumerate(actual, start=1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (word != other),
                )
            )
        previous = current
    return previous[-1] / len(expected)


async def run(audio: Path, reference: str, terms: list[str]) -> dict:
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise ValueError("Configure the backend transcription provider first")
    pcm = audio.read_bytes()
    if not pcm or len(pcm) % 2 or len(pcm) > 60 * 48000:
        raise ValueError("Use up to 60 seconds of mono 24 kHz PCM16 LE audio")
    client = OpenAIRealtimeTranscriber(
        key, settings.openai_realtime_model, vocabulary=terms
    )
    events = []

    async def consume():
        async for event in client.events():
            if event.is_final:
                events.append(event)

    started = time.monotonic()
    async with asyncio.timeout(100):
        await client.connect()
        task = asyncio.create_task(consume())
        try:
            for offset in range(0, len(pcm), 4800):
                await client.send_audio(pcm[offset : offset + 4800])
                await asyncio.sleep(0.1)
            await client.finish()
            await task
        finally:
            await client.close()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    transcript = " ".join(event.text for event in events)
    if not transcript:
        raise ValueError("The provider returned no transcript")
    return {
        "fixture": "explicit test recording; no real meeting capture",
        "model": settings.openai_realtime_model,
        "reference": reference,
        "transcript": transcript,
        "word_error_rate": round(word_error_rate(reference, transcript), 4),
        "normalization": "case and punctuation only; numeric/date formatting counts as word edits",
        "audio_seconds": round(len(pcm) / 48000, 2),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "segments": [
            {
                "text": event.text,
                "confidence": event.confidence,
                "needs_review": event.needs_review,
            }
            for event in events
        ],
        "limits": "A single synthetic fixture is not evidence of accuracy on real calls or other languages.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--vocabulary", action="append", default=[])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.audio, args.reference, args.vocabulary))
    except Exception as exc:
        print(
            f"Transcription evaluation failed ({type(exc).__name__}). Check model access and audio; no provider details logged."
        )
        sys.exit(1)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(encoded + "\n")
    print(encoded)
