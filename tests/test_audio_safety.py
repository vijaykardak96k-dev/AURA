"""
tests/test_audio_safety.py

The audio sanitation layer that stands between the microphone and
Whisper. These tests exist because of three concrete failures in the
real logs:

    RuntimeWarning: overflow encountered in square
    RuntimeWarning: invalid value encountered in matmul
    Python backend exited with code=3221225477  (access violation)

All three trace back to raw microphone buffers containing NaN/Inf or
out-of-range samples reaching NumPy and then CTranslate2. The fix is to
guarantee that never happens, so these tests assert the guarantee
directly rather than asserting that a warning was suppressed.

No microphone, no Whisper, no audio drivers needed.
"""

import numpy as np
import pytest

from app.voice.audio_safety import (
    AudioValidationError,
    is_effectively_silent,
    is_probable_hallucination,
    looks_like_speech,
    rms_energy,
    sanitize_audio,
)

SAMPLE_RATE = 16000


# ----------------------------------------------------------- sanitize_audio

def test_sanitize_returns_finite_float32_in_range():
    audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype="float32")
    out = sanitize_audio(np, audio)

    assert out.dtype == np.float32
    assert np.isfinite(out).all()
    assert out.max() <= 1.0 and out.min() >= -1.0


def test_sanitize_strips_nan_and_inf():
    """This is the exact shape of buffer that produced 'invalid value
    encountered in matmul' and then killed the process."""
    audio = np.array([np.nan, np.inf, -np.inf, 0.4], dtype="float32")
    out = sanitize_audio(np, audio)

    assert np.isfinite(out).all()
    assert out[3] == pytest.approx(0.4, abs=1e-6)


def test_sanitize_clips_a_few_wild_samples_without_destroying_real_audio():
    """
    A handful of corrupt samples must be clipped, NOT used to rescale the
    whole buffer — rescaling on one bad sample would make an otherwise
    perfectly good recording inaudible to Whisper.
    """
    audio = np.concatenate([
        np.full(1000, 0.3, dtype="float32"),
        np.array([1e9], dtype="float32"),
    ])

    out = sanitize_audio(np, audio)

    assert np.isfinite(out).all()
    assert out[:1000].mean() == pytest.approx(0.3, abs=1e-3)
    assert abs(out[-1]) <= 1.0


def test_sanitize_rescales_a_genuinely_integer_scaled_buffer():
    """int16 samples handed back as floats: the whole buffer is out of
    range, so rescaling (not clipping) is correct — clipping would turn
    speech into a square wave."""
    audio = (np.sin(np.linspace(0, 20, 2000)) * 20000).astype("float32")

    out = sanitize_audio(np, audio)

    assert out.max() <= 1.0 and out.min() >= -1.0
    # Still recognisably a sine, not a clipped square wave.
    assert 0.1 < float(np.abs(out).mean()) < 0.9


def test_sanitize_mixes_down_multichannel_audio():
    stereo = np.array([[0.2, 0.4], [0.6, 0.8]], dtype="float32")
    out = sanitize_audio(np, stereo)

    assert out.ndim == 1
    assert len(out) == 2


def test_sanitize_rejects_empty_and_all_nan_buffers():
    with pytest.raises(AudioValidationError):
        sanitize_audio(np, np.array([], dtype="float32"))

    with pytest.raises(AudioValidationError):
        sanitize_audio(np, np.array([np.nan, np.nan], dtype="float32"))

    with pytest.raises(AudioValidationError):
        sanitize_audio(np, None)


def test_sanitize_rejects_an_implausibly_long_buffer():
    """A stuck stream, not a sentence."""
    with pytest.raises(AudioValidationError):
        sanitize_audio(np, np.zeros(100, dtype="float32"), max_samples=10)


# --------------------------------------------------------------- rms_energy

def test_rms_energy_does_not_overflow_on_huge_values():
    """np.square() on raw float32 of this magnitude is exactly what
    produced 'overflow encountered in square'."""
    audio = np.full(100, 3e38, dtype="float32")

    value = rms_energy(np, audio)

    assert np.isfinite(value)


def test_rms_energy_is_zero_for_empty_or_broken_input():
    assert rms_energy(np, np.array([], dtype="float32")) == 0.0
    assert rms_energy(np, np.array([np.nan, np.inf], dtype="float32")) == 0.0
    assert rms_energy(np, None) == 0.0


def test_rms_energy_tracks_loudness():
    quiet = rms_energy(np, np.full(100, 0.001, dtype="float32"))
    loud = rms_energy(np, np.full(100, 0.5, dtype="float32"))
    assert loud > quiet


# ----------------------------------------------------------- speech gating

def _tone(seconds, amplitude=0.2):
    n = int(seconds * SAMPLE_RATE)
    return (np.sin(np.linspace(0, 400, n)) * amplitude).astype("float32")


def test_silence_is_not_speech():
    silence = np.zeros(SAMPLE_RATE, dtype="float32")

    assert is_effectively_silent(np, silence)
    assert not looks_like_speech(np, silence, SAMPLE_RATE)


def test_a_very_short_blip_is_not_speech():
    """A click or a cough — the old code would send this to Whisper and
    get back a hallucinated transcript."""
    blip = _tone(0.1)

    assert not looks_like_speech(np, blip, SAMPLE_RATE)


def test_sustained_sound_counts_as_speech():
    assert looks_like_speech(np, _tone(1.2), SAMPLE_RATE)


def test_one_loud_spike_in_silence_is_not_speech():
    """Loud enough on average, long enough — but only one voiced block,
    so the voiced-ratio check rejects it. This is a door slam."""
    audio = np.zeros(SAMPLE_RATE, dtype="float32")
    audio[:400] = 0.9

    assert not looks_like_speech(np, audio, SAMPLE_RATE)


# ------------------------------------------------------ hallucination filter

@pytest.mark.parametrize("text", [
    "", "   ", ".", "...", "you", "Thank you.", "Thanks for watching!",
    "[Music]", "Subtitles by the Amara.org community",
])
def test_known_whisper_phantoms_are_rejected(text):
    assert is_probable_hallucination(text)


@pytest.mark.parametrize("text", [
    "open task manager",
    "what's my cpu usage",
    "search youtube for kubernetes tutorials",
])
def test_real_commands_survive_the_filter(text):
    assert not is_probable_hallucination(text)


def test_wake_mode_never_discards_something_containing_the_wake_word():
    """wake_word.detect_wake_word() must stay the only wake gate — this
    filter must not quietly eat a wake phrase before it gets there."""
    assert not is_probable_hallucination("Aura", wake_mode=True)
    assert not is_probable_hallucination("okay aura", wake_mode=True)
