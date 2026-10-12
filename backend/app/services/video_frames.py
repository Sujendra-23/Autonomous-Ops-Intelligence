"""Screen-recording keyframes → timestamped captions → text for the extraction pipeline.

ffmpeg samples frames (scene-change detection, topped up with a fixed interval when a
recording is mostly static), the vision model captions each frame, and the captions are
rendered as ``[mm:ss] caption`` lines that can stand alone or sit beside a Whisper
transcript of the same recording.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.llm.client import LLMClient, LLMError
from app.llm.vision import ImagePart
from app.logging import get_logger
from app.metrics import VIDEO_FRAMES

logger = get_logger("app.video_frames")

SCENE_THRESHOLD = 0.3
INTERVAL_SECONDS = 30.0
MAX_FRAMES = 40
MIN_SCENE_FRAMES = 2
FRAME_WIDTH = 1280
CAPTION_CONCURRENCY = 4
FFMPEG_TIMEOUT = 300

_PTS_RE = re.compile(r"pts_time:(-?\d+(?:\.\d+)?)")


class FrameSamplingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Keyframe:
    timestamp: float
    image: ImagePart


@dataclass(frozen=True)
class FrameCaption:
    timestamp: float
    text: str


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _run_select(path: Path, select: str, out_dir: Path) -> list[float]:
    """Run ffmpeg with a ``select`` expression; return pts_time of each written frame."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FrameSamplingError("ffmpeg is not available")
    vf = f"select='{select}',showinfo,scale='min({FRAME_WIDTH},iw)':-2"
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                ffmpeg,
                *("-nostdin", "-v", "info", "-protocol_whitelist", "file,pipe"),
                *("-i", str(path), "-an", "-vf", vf, "-fps_mode", "passthrough"),
                *("-q:v", "4", str(out_dir / "f_%04d.jpg")),
            ],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT,
            text=True,
        )
    except subprocess.TimeoutExpired:
        raise FrameSamplingError("Frame sampling timed out; use a shorter recording") from None
    except FileNotFoundError:
        raise FrameSamplingError("ffmpeg is not available") from None
    if result.returncode != 0:
        raise FrameSamplingError("Recording could not be decoded")
    return [float(m.group(1)) for m in _PTS_RE.finditer(result.stderr)]


def _collect(out_dir: Path, times: list[float]) -> list[Keyframe]:
    files = sorted(out_dir.glob("f_*.jpg"))
    frames = [
        Keyframe(t, ImagePart(f.read_bytes(), "image/jpeg", label=format_timestamp(t)))
        for t, f in zip(times, files, strict=False)
    ]
    for f in files:
        f.unlink()
    return frames


def _thin(frames: list[Keyframe], limit: int) -> list[Keyframe]:
    if len(frames) <= limit:
        return frames
    step = (len(frames) - 1) / (limit - 1)
    return [frames[round(i * step)] for i in range(limit)]


def sample_keyframes(
    path: Path,
    *,
    mode: str = "scene",
    scene_threshold: float = SCENE_THRESHOLD,
    interval: float = INTERVAL_SECONDS,
    max_frames: int = MAX_FRAMES,
) -> list[Keyframe]:
    """Sample frames in timestamp order.

    ``mode="scene"`` keeps the first frame plus every frame whose scene score exceeds
    ``scene_threshold``; if that yields fewer than two frames (a static screen) it falls
    back to ``mode="interval"``, one frame per ``interval`` seconds. At most ``max_frames``
    are returned, thinned evenly across the recording.
    """
    if mode not in {"scene", "interval"}:
        raise ValueError("mode must be 'scene' or 'interval'")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        used = mode
        frames: list[Keyframe] = []
        if mode == "scene":
            expr = f"eq(n,0)+gt(scene,{scene_threshold})"
            frames = _collect(out, _run_select(path, expr, out))
            if len(frames) < MIN_SCENE_FRAMES:
                used = "interval"
        if used == "interval":
            expr = f"isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})"
            frames = _collect(out, _run_select(path, expr, out))
    if not frames:
        raise FrameSamplingError("No video frames found in the recording")
    frames = _thin(frames, max_frames)
    VIDEO_FRAMES.labels(used).inc(len(frames))
    return frames


async def caption_frames(client: LLMClient, frames: list[Keyframe]) -> list[FrameCaption]:
    """Caption frames concurrently. A frame whose caption fails is skipped, not fatal."""
    gate = asyncio.Semaphore(CAPTION_CONCURRENCY)

    async def one(frame: Keyframe) -> FrameCaption | None:
        async with gate:
            try:
                text = await client.caption_image(
                    frame.image, timestamp=format_timestamp(frame.timestamp)
                )
            except LLMError as exc:
                logger.warning("video.caption_failed", timestamp=frame.timestamp, error=str(exc))
                return None
        return FrameCaption(frame.timestamp, text) if text else None

    results = await asyncio.gather(*(one(f) for f in frames))
    captions = [c for c in results if c]
    if frames and not captions:
        raise LLMError("No frame could be captioned")
    return captions


def merge_captions(captions: list[FrameCaption], audio_transcript: str | None = None) -> str:
    """Render captions as timestamped lines, after any audio transcript."""
    lines = [f"[{format_timestamp(c.timestamp)}] {c.text.strip()}" for c in captions]
    visual = "## Screen captions\n" + "\n".join(lines)
    if audio_transcript and audio_transcript.strip():
        return f"## Audio transcript\n{audio_transcript.strip()}\n\n{visual}"
    return visual


async def recording_to_text(
    client: LLMClient,
    path: Path,
    *,
    audio_transcript: str | None = None,
    mode: str = "scene",
    interval: float = INTERVAL_SECONDS,
    max_frames: int = MAX_FRAMES,
) -> str:
    loop = asyncio.get_running_loop()
    frames = await loop.run_in_executor(
        None,
        lambda: sample_keyframes(path, mode=mode, interval=interval, max_frames=max_frames),
    )
    captions = await caption_frames(client, frames)
    return merge_captions(captions, audio_transcript)
