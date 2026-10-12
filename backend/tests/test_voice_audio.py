"""Telephony audio: mu-law decode and streaming resampling (pure, no I/O)."""

from array import array

import pytest

from app.services.audio import (
    LinearResampler,
    TwilioAudioConverter,
    mulaw_decode,
    mulaw_encode,
    pcm16_bytes_to_samples,
)


def test_mulaw_known_values():
    # G.711 reference points: 0xFF/0x7F are (near-)silence, 0x00/0x80 are full scale.
    assert list(mulaw_decode(bytes([0xFF, 0x7F, 0x00, 0x80]))) == [0, 0, -32124, 32124]


def test_mulaw_decode_is_monotonic_in_magnitude():
    positive = [mulaw_decode(bytes([b]))[0] for b in range(0x80, 0xFF + 1)]
    assert positive == sorted(positive, reverse=True)


@pytest.mark.parametrize("sample", [0, 1, 100, -100, 1000, -1000, 8000, -8000, 30000, -30000])
def test_mulaw_roundtrip_within_quantization_error(sample):
    decoded = mulaw_decode(mulaw_encode(array("h", [sample])))[0]
    # mu-law is logarithmic: error grows with magnitude, bounded by ~3% (+ the bias floor).
    assert abs(decoded - sample) <= max(40, abs(sample) * 0.04)


def test_resampler_doubles_rate_and_interpolates():
    out = LinearResampler(8000, 16000).process(array("h", [0, 100, 200]))
    assert list(out) == [0, 50, 100, 150]  # last input sample is held for the next frame


def test_resampler_is_continuous_across_frames():
    signal = array("h", range(0, 1000, 10))  # 100 samples, a straight ramp
    whole = LinearResampler(8000, 16000).process(signal)
    chunked = LinearResampler(8000, 16000)
    parts = array("h")
    for i in range(0, len(signal), 7):  # awkward frame size on purpose
        parts.extend(chunked.process(signal[i : i + 7]))
    assert list(parts) == list(whole)


def test_resampler_output_length_matches_rate_ratio():
    for dst, factor in ((16000, 2), (24000, 3)):
        resampler = LinearResampler(8000, dst)
        total = sum(len(resampler.process(array("h", [0] * 160))) for _ in range(50))
        # Within one input sample of the ideal ratio.
        assert abs(total - 160 * 50 * factor) <= factor


def test_resampler_rejects_bad_rates_and_empty_input():
    with pytest.raises(ValueError):
        LinearResampler(0, 16000)
    assert len(LinearResampler(8000, 16000).process(array("h"))) == 0


def test_twilio_converter_emits_little_endian_pcm16_at_target_rate():
    frame = mulaw_encode(array("h", [0] * 160))  # one 20 ms Twilio frame
    converter = TwilioAudioConverter(16000)
    first = pcm16_bytes_to_samples(converter.convert(frame))
    second = pcm16_bytes_to_samples(converter.convert(frame))
    # The resampler holds back the final input sample until the next frame arrives.
    assert (len(first), len(second)) == (318, 320)
    assert not any(first) and not any(second)
    assert len(converter.convert(frame)) == 320 * 2  # PCM16: two bytes per sample

    loud = mulaw_encode(array("h", [20000] * 160))
    samples = pcm16_bytes_to_samples(TwilioAudioConverter(8000).convert(loud))
    assert len(samples) == 160 and all(abs(s - 20000) < 1000 for s in samples)
