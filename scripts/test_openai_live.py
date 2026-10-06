"""Live OpenAI smoke test using a short, explicitly supplied PCM16 audio fixture.

Run from the repository root; no database, Chrome capture, or integration dispatch.
This makes billable transcription and extraction API calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from app.config import get_settings  # noqa: E402
from app.llm.client import OpenAIClient  # noqa: E402
from app.services.live_stt import OpenAIRealtimeTranscriber  # noqa: E402


async def run(audio: Path, meeting_date: str) -> dict:
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise RuntimeError("Configure OPENAI_API_KEY on the backend first")
    pcm = audio.read_bytes()
    if not pcm or len(pcm) % 2 or len(pcm) > 30 * 48000:
        raise RuntimeError("Use 1–30 seconds of mono 24 kHz PCM16 little-endian audio")
    transcriber = OpenAIRealtimeTranscriber(key, settings.openai_realtime_model)
    started = time.monotonic()
    finals: list[str] = []
    interim_count = 0
    first_final = None

    async def consume() -> None:
        nonlocal interim_count, first_final
        async for event in transcriber.events():
            if event.is_final:
                finals.append(event.text)
                if first_final is None:
                    first_final = round(time.monotonic() - started, 2)
            else:
                interim_count += 1

    async with asyncio.timeout(90):
        await transcriber.connect()
        consumer = asyncio.create_task(consume())
        try:
            # Pace 100 ms frames to exercise streaming rather than file upload.
            for offset in range(0, len(pcm), 4800):
                await transcriber.send_audio(pcm[offset : offset + 4800])
                await asyncio.sleep(0.1)
            await transcriber.finish()
            await consumer
        finally:
            await transcriber.close()
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        transcript = " ".join(finals)
        if not transcript:
            raise RuntimeError("No final transcript was received")
        result = await OpenAIClient().extract(
            transcript, meeting_title="Live smoke test", meeting_date=meeting_date
        )
    return {
        "transcription_model": settings.openai_realtime_model,
        "extraction_model": settings.openai_model,
        "meeting_date": meeting_date,
        "audio_seconds": round(len(pcm) / 48000, 2),
        "interim_events": interim_count,
        "first_final_seconds": first_final,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "transcript": transcript,
        "result": result.model_dump(mode="json"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audio", type=Path, help="Mono 24 kHz signed PCM16 raw audio, up to 30 seconds"
    )
    parser.add_argument("--meeting-date", default=datetime.now(UTC).isoformat())
    parser.add_argument(
        "--output", type=Path, help="Optional JSON report (contains transcribed text)"
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.audio, args.meeting_date))
    except Exception as exc:
        # SDK exceptions can embed headers or private provider responses.
        print(
            f"Live test failed ({type(exc).__name__}); check credentials, model access, and audio."
        )
        sys.exit(1)
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n")
    print(encoded)
