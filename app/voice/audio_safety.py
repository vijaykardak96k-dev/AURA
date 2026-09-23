"""
app/voice/audio_safety.py

Small, dependency-light helpers that make the microphone -> Whisper path
safe on Windows.

Why this file exists
--------------------
The logs showed three related failures that all trace back to raw
microphone buffers being handed straight to NumPy/Whisper:

    RuntimeWarning: overflow encountered in square
    RuntimeWarning: invalid value encountered in matmul
    Python backend exited with code=3221225477   (STATUS_ACCESS_VIOLATION)

`sounddevice` can hand back float32 blocks containing NaN/Inf or values
well outside [-1.0, 1.0] when a device glitches, a sample-rate conversion
misbehaves, or the stream overflows. Squaring those overflows float32;
feeding them into CTranslate2's matmul produces NaN and can crash the
native library outright — which is what a 0xC0000005 exit code is.

The fix is NOT to silence the warning. It is to make sure nothing
invalid ever reaches Whisper:

    sanitize_audio()  -> always returns finite float32 in [-1, 1]
    rms_energy()      -> overflow-proof RMS (computed in float64)
    is_effectively_silent() / looks_like_speech() -> cheap gating so
        silence and noise never become a transcription request

Everything here is pure and testable without a microphone; see
tests/test_audio_safety.py.
"""

from __future__ import annotations

# Absolute ceiling for a single command recording. Anything longer is
# almost certainly a stuck stream rather than a real sentence.
MAX_SAFE_SAMPLES = 16000 * 60  # 60 seconds at 16 kHz


class AudioValidationError(Exception):
    """Raised when an audio buffer is unusable and must be discarded."""


def sanitize_audio(np, audio, *, max_samples: int = MAX_SAFE_SAMPLES):
    """
    Return a finite, contiguous float32 array in [-1.0, 1.0].

    `np` is passed in rather than imported at module scope so this module
    stays importable on a machine with no NumPy (the rest of AURA is
    designed to degrade to typed-only interaction in that case).

    Raises AudioValidationError if the buffer is empty, entirely
    non-finite, or absurdly large — the caller should discard it, log it,
    and keep listening rather than crash.
    """
    if audio is None:
        raise AudioValidationError("audio buffer is None")

    array = np.asarray(audio)

    if array.size == 0:
        raise AudioValidationError("audio buffer is empty")

    if array.ndim > 1:
        # Mono-ise: average channels rather than dropping all but the first.
        array = array.reshape(array.shape[0], -1).mean(axis=1)

    array = array.reshape(-1)

    if array.size > max_samples:
        raise AudioValidationError(
            f"audio buffer is implausibly long ({array.size} samples)"
        )

    # Work in float64 while cleaning so intermediate maths can't overflow.
    array = array.astype("float64", copy=False)

    finite = np.isfinite(array)
    if not finite.any():
        raise AudioValidationError("audio buffer contains no finite samples")

    if not finite.all():
        array = np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)

    magnitude = np.abs(array)
    peak = float(np.max(magnitude))

    if peak > 4.0:
        # Either the whole buffer is integer-scaled (int16 samples handed
        # back as floats), or a handful of samples are corrupt. Use the
        # 99th percentile to tell the two apart: rescaling on the strength
        # of one bad sample would make a perfectly good recording
        # inaudible, and clipping genuine int16 audio would turn it into a
        # square wave.
        p99 = float(np.percentile(magnitude, 99))
        if p99 > 4.0:
            array = array / max(peak, 1e-9)

    array = np.clip(array, -1.0, 1.0)

    return np.ascontiguousarray(array, dtype="float32")


def rms_energy(np, block) -> float:
    """
    Overflow-proof RMS of one audio block.

    Returns 0.0 for empty/invalid input instead of raising — VAD runs on
    every 50 ms block and must never be the thing that kills the loop.
    """
    try:
        array = np.asarray(block).reshape(-1)
        if array.size == 0:
            return 0.0

        array = array.astype("float64", copy=False)
        array = array[np.isfinite(array)]
        if array.size == 0:
            return 0.0

        array = np.clip(array, -4.0, 4.0)
        value = float(np.sqrt(np.mean(np.square(array))))

        if value != value:  # NaN
            return 0.0
        return value
    except Exception:
        return 0.0


def is_effectively_silent(np, audio, threshold: float = 0.006) -> bool:
    """True when a whole recording is too quiet to be a real command."""
    return rms_energy(np, audio) < threshold


def looks_like_speech(
    np,
    audio,
    sample_rate: int,
    *,
    min_seconds: float = 0.35,
    energy_threshold: float = 0.006,
    min_voiced_ratio: float = 0.10,
    block_seconds: float = 0.05,
) -> bool:
    """
    Cheap pre-Whisper gate: a buffer counts as speech only if it is long
    enough, loud enough overall, AND has a reasonable proportion of
    above-threshold blocks. A single door slam passes the first two
    checks but fails the third, which is what keeps one-off background
    noise out of the intent pipeline.
    """
    try:
        array = np.asarray(audio).reshape(-1)
    except Exception:
        return False

    if array.size < int(min_seconds * sample_rate):
        return False

    if is_effectively_silent(np, array, energy_threshold):
        return False

    block = max(1, int(block_seconds * sample_rate))
    usable = (array.size // block) * block
    if usable < block:
        return False

    blocks = array[:usable].reshape(-1, block)

    try:
        blocks = blocks.astype("float64", copy=False)
        blocks = np.nan_to_num(blocks, nan=0.0, posinf=0.0, neginf=0.0)
        blocks = np.clip(blocks, -4.0, 4.0)
        energies = np.sqrt(np.mean(np.square(blocks), axis=1))
    except Exception:
        return False

    voiced = float((energies >= energy_threshold).mean())
    return voiced >= min_voiced_ratio


# ---------------------------------------------------------------------------
# Whisper hallucination filter
# ---------------------------------------------------------------------------

# Whisper reliably emits these for silence / room tone / music stings.
# They are never real AURA commands, so treating them as "heard nothing"
# is strictly better than sending them to the intent pipeline.
_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you.", "thanks for watching",
    "thanks for watching!", "thank you for watching", "bye", "bye.",
    "okay", "ok", ".", "..", "...", "!", "?", "-", "uh", "um", "hmm",
    "mm", "mhm", "yeah", "subtitles by the amara org community",
    "subscribe", "please subscribe", "the end", "music", "applause",
    "[music]", "[applause]", "[silence]", "(music)", "(applause)",
}


def is_probable_hallucination(text: str, *, wake_mode: bool = False) -> bool:
    """
    True when a transcript is one of Whisper's well-known phantom outputs.

    In wake mode this is deliberately NOT applied to anything containing
    the wake token — `wake_word.detect_wake_word()` remains the only gate
    that decides whether AURA wakes up.
    """
    if not text:
        return True

    import re

    normalized = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if not normalized:
        return True

    if wake_mode and "aura" in normalized:
        return False

    if normalized in _HALLUCINATIONS:
        return True

    # A "transcript" of one or two characters carries no command.
    if len(normalized) <= 2 and not normalized.isdigit():
        return True

    return False
