"""Telephony audio helpers: G.711 mu-law decode and streaming resampling.

Twilio Media Streams delivers 8 kHz mono mu-law. The STT relay in
`live_stt.py` expects linear PCM16 at the provider's rate (16 kHz for Deepgram,
24 kHz for OpenAI Realtime). Python 3.13 removed `audioop`, so this is pure
Python with no extra dependencies; a call is 50 frames/s of 160 samples, which is
well within what a per-sample loop handles.
"""

from __future__ import annotations

import sys
from array import array

TWILIO_SAMPLE_RATE = 8000

_BIAS = 0x84
_CLIP = 32635


def _decode_byte(value: int) -> int:
    value = ~value & 0xFF
    sample = (((value & 0x0F) << 3) + _BIAS) << ((value & 0x70) >> 4)
    sample -= _BIAS
    return -sample if value & 0x80 else sample


_DECODE_TABLE = array("h", (_decode_byte(i) for i in range(256)))


def _to_little_endian(samples: array) -> bytes:
    if sys.byteorder == "big":
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def _from_little_endian(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def mulaw_decode(data: bytes) -> array:
    """Decode G.711 mu-law bytes to signed 16-bit samples."""
    return array("h", (_DECODE_TABLE[b] for b in data))


def mulaw_encode(samples: array) -> bytes:
    """Encode signed 16-bit samples to mu-law (used by tests and fixtures)."""
    out = bytearray()
    for sample in samples:
        sign = 0x80 if sample < 0 else 0
        magnitude = min(abs(sample), _CLIP) + _BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not magnitude & mask:
            exponent -= 1
            mask >>= 1
        mantissa = (magnitude >> (exponent + 3)) & 0x0F
        out.append(~(sign | (exponent << 4) | mantissa) & 0xFF)
    return bytes(out)


class LinearResampler:
    """Streaming linear-interpolation resampler for PCM16 mono.

    State carries across `process()` calls so frame boundaries do not introduce
    clicks or drop samples. Linear interpolation is adequate for speech going to
    an STT model; it is not a music-grade resampler.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("sample rates must be positive")
        self._step = src_rate / dst_rate
        self._prev: int | None = None
        self._pos = 0.0

    def process(self, samples: array) -> array:
        if not samples:
            return array("h")
        buf = samples if self._prev is None else array("h", [self._prev, *samples])
        out = array("h")
        last = len(buf) - 1
        t = self._pos
        while int(t) < last:
            i = int(t)
            frac = t - i
            out.append(round(buf[i] + (buf[i + 1] - buf[i]) * frac))
            t += self._step
        self._prev = buf[last]
        self._pos = t - last
        return out


class TwilioAudioConverter:
    """mu-law 8 kHz frames in, PCM16 little-endian at the STT provider's rate out."""

    def __init__(self, target_rate: int) -> None:
        self._resampler = (
            None
            if target_rate == TWILIO_SAMPLE_RATE
            else LinearResampler(TWILIO_SAMPLE_RATE, target_rate)
        )

    def convert(self, mulaw: bytes) -> bytes:
        pcm = mulaw_decode(mulaw)
        if self._resampler is not None:
            pcm = self._resampler.process(pcm)
        return _to_little_endian(pcm)


def pcm16_bytes_to_samples(data: bytes) -> array:
    return _from_little_endian(data)
