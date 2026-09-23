"""tests/test_prosody.py — pure logic; silence_samples() is the only
function that touches numpy, and numpy is a hard requirement of AURA's
audio path anyway (see requirements.txt), so it's imported directly."""

import numpy as np
import pytest

from app.voice.prosody import (
    LENGTH_SCALE_BOUNDS,
    NOISE_BOUNDS,
    chunk_for_speech,
    get_prosody,
    silence_samples,
)


def test_get_prosody_neutral_matches_piper_natural_defaults():
    params = get_prosody("neutral")
    assert params["length_scale"] == pytest.approx(1.0, abs=0.05)
    assert LENGTH_SCALE_BOUNDS[0] <= params["length_scale"] <= LENGTH_SCALE_BOUNDS[1]


def test_get_prosody_unknown_emotion_falls_back_to_neutral_defaults():
    params = get_prosody("furious")  # not in EMOTIONS at all
    neutral = get_prosody("neutral")
    assert params == neutral


def test_get_prosody_never_exceeds_hard_bounds():
    for emotion in ("neutral", "warm", "happy", "excited", "playful", "calm",
                     "concerned", "serious", "empathetic", "confident", "bogus"):
        params = get_prosody(emotion, jitter=True, seed=1)
        assert LENGTH_SCALE_BOUNDS[0] <= params["length_scale"] <= LENGTH_SCALE_BOUNDS[1]
        assert NOISE_BOUNDS[0] <= params["noise_scale"] <= NOISE_BOUNDS[1]
        assert NOISE_BOUNDS[0] <= params["noise_w"] <= NOISE_BOUNDS[1]


def test_get_prosody_jitter_is_deterministic_with_seed():
    a = get_prosody("excited", jitter=True, seed=42)
    b = get_prosody("excited", jitter=True, seed=42)
    assert a == b


def test_get_prosody_pitch_shift_is_zero_when_disabled():
    # PITCH_SHIFT_ENABLED defaults to False in config/prosody_config.py
    params = get_prosody("happy")
    assert params["pitch_shift"] == 0.0


def test_chunk_for_speech_empty_text_returns_empty_list():
    assert chunk_for_speech("") == []
    assert chunk_for_speech("   ") == []


def test_chunk_for_speech_no_punctuation_is_one_chunk_with_no_pause():
    chunks = chunk_for_speech("open the browser")
    assert chunks == [("open the browser", 0)]


def test_chunk_for_speech_splits_on_punctuation_with_pauses():
    chunks = chunk_for_speech("I'm sorry to hear that... Do you want to talk about it?",
                               emotion="empathetic")
    texts = [c[0] for c in chunks]
    assert texts == ["I'm sorry to hear that", "Do you want to talk about it"]
    # Both pauses must be positive; empathetic's pause_scale (>1.0) means
    # they must exceed the raw base pause for "..." and "?".
    assert all(pause_ms > 0 for _, pause_ms in chunks)


def test_chunk_for_speech_pause_scale_changes_pause_length():
    neutral_chunks = chunk_for_speech("Wait, really?", emotion="neutral")
    excited_chunks = chunk_for_speech("Wait, really?", emotion="excited")
    # excited's pause_scale (0.75) is shorter than neutral's (1.0) for the
    # comma pause specifically.
    assert excited_chunks[0][1] < neutral_chunks[0][1]


def test_silence_samples_returns_correct_length_and_is_silent():
    arr = silence_samples(pause_ms=100, sample_rate=22050)
    assert isinstance(arr, np.ndarray)
    assert len(arr) == int(22050 * 100 / 1000)
    assert np.all(arr == 0.0)


def test_silence_samples_zero_ms_is_empty():
    arr = silence_samples(pause_ms=0, sample_rate=22050)
    assert len(arr) == 0
