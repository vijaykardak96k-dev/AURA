"""
app/voice/prosody.py

Turns an emotion label into (a) Piper synthesis parameters and (b) a list
of text chunks with a pause duration after each one. Pure functions only
— no audio I/O, no sounddevice, no PiperVoice — so this is fully
unit-testable without hardware (see tests/test_prosody.py).

Piper itself has no emotional model; this is prosody shaping only:
speaking rate, voice-texture knobs, and inserted silence around
punctuation. It is deliberately subtle — small pauses and a slightly
different pace, not a theatrical read. app/voice/piper_tts.py is the only
caller that turns this into actual sound.
"""

import random
import re
from typing import List, Optional, Tuple

try:
    from config.prosody_config import PROSODY, BASE_PAUSES_MS, PITCH_SHIFT_ENABLED
except Exception:  # pragma: no cover - defensive fallback if config/ isn't
    # importable (e.g. run from an unusual working directory). Keeps AURA
    # speaking with neutral-ish defaults instead of crashing on import.
    PROSODY = {}
    BASE_PAUSES_MS = {",": 120, ";": 160, ":": 160, "...": 320, "\u2014": 220,
                       ".": 260, "!": 260, "?": 260}
    PITCH_SHIFT_ENABLED = False

# Hard bounds — prosody values can never leave this range, whatever the
# config file says, so a bad tune can slow AURA down or speed it up but
# can never produce unintelligible speech.
LENGTH_SCALE_BOUNDS = (0.8, 1.4)
NOISE_BOUNDS = (0.1, 1.2)
PITCH_SHIFT_BOUNDS = (-0.05, 0.05)  # +/- 5%, and only used if enabled

_NEUTRAL_DEFAULTS = {"length_scale": 1.0, "noise_scale": 0.667, "noise_w": 0.8,
                      "pause_scale": 1.0, "pitch_shift": 0.0}

# Small deterministic-by-default jitter on speaking rate per chunk, so
# consecutive sentences don't sound metronomic. Off (0.0) unless a caller
# passes jitter=True, and always reproducible when a seed is given —
# tests pass seed=0 to assert exact output.
_JITTER_RANGE = 0.03


def _clamp(value: float, bounds: Tuple[float, float]) -> float:
    lo, hi = bounds
    return max(lo, min(hi, value))


def get_prosody(emotion: str, jitter: bool = False, seed: Optional[int] = None) -> dict:
    """
    Returns a clamped dict of {length_scale, noise_scale, noise_w,
    pitch_shift} for the given emotion. Unknown/invalid emotions fall
    back to neutral defaults. Never raises.
    """
    base = PROSODY.get(emotion, _NEUTRAL_DEFAULTS) if emotion else _NEUTRAL_DEFAULTS

    length_scale = float(base.get("length_scale", 1.0))
    noise_scale = float(base.get("noise_scale", 0.667))
    noise_w = float(base.get("noise_w", 0.8))
    pitch_shift = float(base.get("pitch_shift", 0.0))

    if jitter:
        rng = random.Random(seed)
        length_scale += rng.uniform(-_JITTER_RANGE, _JITTER_RANGE)

    length_scale = _clamp(length_scale, LENGTH_SCALE_BOUNDS)
    noise_scale = _clamp(noise_scale, NOISE_BOUNDS)
    noise_w = _clamp(noise_w, NOISE_BOUNDS)

    if PITCH_SHIFT_ENABLED:
        pitch_shift = _clamp(pitch_shift, PITCH_SHIFT_BOUNDS)
    else:
        pitch_shift = 0.0

    return {
        "length_scale": length_scale,
        "noise_scale": noise_scale,
        "noise_w": noise_w,
        "pitch_shift": pitch_shift,
    }


# Longest markers first so "..." isn't matched as three separate "."s.
_PUNCT_ORDER = ("...", "\u2014", ",", ";", ":", "!", "?", ".")
_SPLIT_RE = re.compile(
    r"(\.\.\.|\u2014|[,;:!?.])"
)


def chunk_for_speech(text: str, emotion: str = "neutral") -> List[Tuple[str, int]]:
    """
    Splits `text` into (chunk_text, pause_ms_after) pairs. Pauses come
    from BASE_PAUSES_MS scaled by the emotion's pause_scale, then rounded
    to whole milliseconds. The final chunk always carries the sentence's
    trailing pause too (rather than zero), so a synthesized reply doesn't
    end in dead silence but also doesn't leave an extra unwanted pause
    for the caller to trim.

    Empty/whitespace-only input returns an empty list. Never raises.
    """
    text = (text or "").strip()
    if not text:
        return []

    scale = 1.0
    if emotion in PROSODY:
        scale = float(PROSODY[emotion].get("pause_scale", 1.0))
    scale = max(0.3, min(2.0, scale))  # never silence a reply, never drag it out absurdly

    parts = _SPLIT_RE.split(text)

    chunks: List[Tuple[str, int]] = []
    buf = ""
    for part in parts:
        if part is None or part == "":
            continue
        if part in BASE_PAUSES_MS:
            piece = buf.strip()
            buf = ""
            pause_ms = int(round(BASE_PAUSES_MS[part] * scale))
            if piece:
                chunks.append((piece, pause_ms))
            elif chunks:
                # Punctuation with no preceding text (e.g. stray "..").
                # extend the previous chunk's pause instead of emitting
                # an empty utterance to Piper.
                last_text, last_pause = chunks[-1]
                chunks[-1] = (last_text, last_pause + pause_ms)
        else:
            buf += part

    remainder = buf.strip()
    if remainder:
        chunks.append((remainder, 0))

    return chunks


def silence_samples(pause_ms: int, sample_rate: int):
    """
    Returns a 1-D numpy float32 array of `pause_ms` milliseconds of
    silence at `sample_rate`. Imports numpy lazily so this module stays
    importable (and its pure functions testable) without numpy installed.
    """
    import numpy as np
    n = max(0, int(sample_rate * pause_ms / 1000))
    return np.zeros(n, dtype="float32")
