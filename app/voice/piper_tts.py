"""
app/voice/piper_tts.py

Piper (https://github.com/OHF-Voice/piper1-gpl) neural TTS via the
`piper-tts` PyPI package's Python API — NOT by shelling out to a `piper`
binary. This distinction is the whole point of this module.

Why: a subprocess-per-utterance integration (`subprocess.run(["piper",
"--model", path, ...])`) reloads the entire ONNX model from disk on
every single call. For a model of any real size that's easily a
multi-second cost, and it happens for EVERY response, not just once —
which matches, symptom-for-symptom, "text appears, then a 2-3 second
wait before the voice starts" on every utterance. This module instead:

  1. Loads the model ONCE via PiperVoice.load(), ideally during app
     startup via preload() — see app/main.py / app/ui/main_window.py,
     which call this before speaking the startup greeting so the
     greeting itself doesn't pay the model-load cost either.
  2. Synthesizes directly to an in-memory float32 numpy array
     (piper.voice.AudioChunk.audio_float_array) and plays it with
     `sounddevice.play()` — no WAV file written to disk, no subprocess
     spawned, for any utterance after the initial load.

If neither a Piper model is configured/available nor `piper-tts` is
installed, this degrades to raising PiperUnavailable so the caller (see
create_tts_engine() in text_to_speech.py) can fall back to pyttsx3
instead of AURA losing voice output entirely.

NOT VERIFIED WITH REAL AUDIO OUTPUT — `piper-tts` itself installs and
imports cleanly in this project's dev sandbox (pip install piper-tts),
but an actual .onnx voice model requires a separate download (typically
from Hugging Face) that this sandbox has no network access to fetch, and
there are no speakers to hear it play even if it could. The
preload-then-synthesize-in-memory ARCHITECTURE is what's been verified
(with a fake voice object standing in for the real model — see
tests/test_piper_tts.py); actual voice quality and timing on real
hardware need confirmation on Windows.
"""

import os
import time
from pathlib import Path
from typing import Optional

from app.utils.logger import get_logger
from app.voice.prosody import chunk_for_speech, get_prosody, silence_samples

logger = get_logger()

DEFAULT_LENGTH_SCALE = 1.0  # Piper's own "speaking rate" knob — 1.0 is the model's natural pace
DEFAULT_VOLUME = 1.0


class PiperUnavailable(Exception):
    """Raised when Piper can't be used right now — missing package, missing
    model file, or a synthesis/playback failure. Caller should fall back
    to another TTS engine rather than treat this as fatal."""


class PiperTextToSpeech:
    """
    Wraps a single, preloaded PiperVoice. Construct once, call preload()
    as early as possible in startup (on a background thread alongside
    other init work — it does not need to block anything else), then
    speak() for every subsequent utterance.
    """

    def __init__(self, model_path: Optional[str] = None, config_path: Optional[str] = None,
                 volume: Optional[float] = None, length_scale: Optional[float] = None):
        self.model_path = model_path if model_path is not None else os.environ.get("PIPER_MODEL_PATH", "")
        self.config_path = config_path if config_path is not None else os.environ.get("PIPER_CONFIG_PATH", "")
        self.volume = volume if volume is not None else float(os.environ.get("AURA_TTS_VOLUME", DEFAULT_VOLUME))
        self.length_scale = length_scale if length_scale is not None else float(
            os.environ.get("PIPER_LENGTH_SCALE", DEFAULT_LENGTH_SCALE)
        )

        self._voice = None  # the loaded PiperVoice, set by preload()
        self._load_failed = False
        self._sample_rate: Optional[int] = None

    # ------------------------------------------------------------- loading

    def preload(self) -> None:
        """
        Loads the ONNX model now. Safe to call once at startup; calling
        it again after a successful load is a cheap no-op. Raises
        PiperUnavailable (never a raw exception) if the package isn't
        installed or the model file can't be found/loaded — callers
        during startup should catch this and fall back, not treat it as
        fatal to the whole app.
        """
        if self._voice is not None:
            return
        if self._load_failed:
            raise PiperUnavailable("Piper previously failed to load; not retrying automatically.")

        if not self.model_path:
            self._load_failed = True
            raise PiperUnavailable(
                "PIPER_MODEL_PATH is not set. Download a voice (.onnx + .onnx.json) and set "
                "PIPER_MODEL_PATH in .env, e.g. PIPER_MODEL_PATH=voices/en_US-lessac-medium.onnx"
            )
        if not Path(self.model_path).exists():
            self._load_failed = True
            raise PiperUnavailable(f"Piper model file not found: {self.model_path}")

        try:
            from piper import PiperVoice
        except ImportError as e:
            self._load_failed = True
            raise PiperUnavailable("piper-tts isn't installed. Run: pip install piper-tts") from e

        try:
            t0 = time.monotonic()
            config_path = self.config_path or None
            self._voice = PiperVoice.load(self.model_path, config_path=config_path)
            self._sample_rate = getattr(self._voice.config, "sample_rate", 22050)
            logger.info(
                f"Piper voice loaded from {self.model_path} in {time.monotonic() - t0:.2f}s "
                f"(sample rate {self._sample_rate} Hz)."
            )
        except Exception as e:
            self._load_failed = True
            raise PiperUnavailable(f"Could not load Piper voice: {e}") from e

    def is_available(self) -> bool:
        if self._voice is not None:
            return True
        try:
            self.preload()
            return True
        except PiperUnavailable:
            return False

    # ----------------------------------------------------------- speaking

    def speak(self, text: str, emotion: Optional[str] = None, prosody: Optional[dict] = None, **kwargs) -> bool:
        """
        Speak text with Piper and return True only when playback completed.
        Returning success lets the caller fall back to pyttsx3 if Piper
        cannot synthesize or play audio.

        `emotion` (optional) selects prosody shaping from
        app/voice/prosody.py — speaking rate, Piper's own noise/texture
        knobs, and inserted pauses around punctuation. `prosody` (optional)
        lets a caller override the resolved prosody dict directly (mainly
        for tests). Both are optional and backward compatible:
        `tts.speak("Hello")` behaves exactly as before (neutral prosody).
        Unknown extra kwargs are accepted and ignored rather than raised,
        so older/newer callers never break this signature.
        """
        if not text or not text.strip():
            return True

        try:
            self.preload()
        except PiperUnavailable as e:
            logger.warning(f"Piper unavailable, cannot speak: {e}")
            return False

        try:
            import numpy as np
            import sounddevice as sd
        except (ImportError, OSError) as e:
            logger.warning(f"Playback library unavailable for Piper output: {e}")
            return False

        resolved_emotion = emotion or "neutral"
        params = prosody if prosody is not None else get_prosody(resolved_emotion)
        logger.info(f"TTS emotion/prosody: {resolved_emotion}")

        try:
            from piper import SynthesisConfig
            has_syn_config = True
        except Exception:
            SynthesisConfig = None
            has_syn_config = False

        def _syn_config_for(length_scale: float):
            if not has_syn_config:
                return None
            try:
                return SynthesisConfig(
                    volume=self.volume,
                    length_scale=length_scale,
                    noise_scale=params["noise_scale"],
                    noise_w=params["noise_w"],
                )
            except TypeError:
                # Older/newer piper-tts SynthesisConfig may not accept
                # noise_scale/noise_w — degrade to length_scale only
                # rather than failing the whole utterance.
                try:
                    return SynthesisConfig(volume=self.volume, length_scale=length_scale)
                except Exception:
                    return None
            except Exception:
                return None

        try:
            chunks_with_pauses = chunk_for_speech(text, resolved_emotion) or [(text.strip(), 0)]

            audio_parts = []
            sample_rate = self._sample_rate or 22050

            for chunk_text, pause_ms in chunks_with_pauses:
                if not chunk_text:
                    continue
                # length_scale is configured per-instance (self.length_scale)
                # as a global user preference; the emotion's own scale
                # modulates it rather than replacing it outright.
                effective_length_scale = self.length_scale * params["length_scale"]
                syn_config = _syn_config_for(effective_length_scale)

                piper_chunks = list(self._voice.synthesize(chunk_text, syn_config=syn_config))
                if not piper_chunks:
                    continue
                audio_parts.append(np.concatenate([c.audio_float_array for c in piper_chunks]))
                sample_rate = piper_chunks[0].sample_rate

                if pause_ms:
                    audio_parts.append(silence_samples(pause_ms, sample_rate))

            if not audio_parts:
                logger.warning("Piper produced no audio chunks.")
                return False

            audio = np.concatenate(audio_parts)
            sd.play(audio, samplerate=sample_rate)
            sd.wait()
            return True
        except Exception as e:
            logger.warning(f"Piper synthesis/playback failed: {e}")
            try:
                sd.stop()
            except Exception:
                pass
            return False

    def stop(self) -> None:
        """Stop Piper sounddevice playback immediately."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception as e:
            logger.debug("Piper stop failed: %s", e)

    def synthesize_to_array(self, text: str):
        """
        Returns (numpy_float32_array, sample_rate) without playing it —
        used by tests, and available for any future caller that wants
        the raw audio (e.g. saving a sample, or a different playback
        path) without duplicating the synthesis logic in speak().
        """
        self.preload()
        import numpy as np
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return np.array([], dtype="float32"), self._sample_rate or 22050
        audio = np.concatenate([c.audio_float_array for c in chunks])
        return audio, chunks[0].sample_rate
