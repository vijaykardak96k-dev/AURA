"""
app/voice/text_to_speech.py

Offline text-to-speech via pyttsx3, which drives the Windows SAPI5 voices
already installed on the system — no download, no API key, no internet.

Voice selection: enumerates installed voices and prefers a natural-
sounding English voice, preferring one that looks female (by name/id
heuristics — SAPI5 exposes no reliable structured gender field across all
installations, so this is a best-effort match on common installed-voice
names like "Zira", "Jenny", "Aria", "Samantha", "Hazel", or the literal
word "female"). Fully configurable via .env:

    AURA_TTS_VOICE=      # optional exact/partial voice name or id to force
    AURA_TTS_RATE=175    # words per minute; keep this natural, not rushed
    AURA_TTS_VOLUME=1.0  # 0.0-1.0

The selection logic (pick_best_voice) is a pure function over plain
voice-like objects, deliberately separated from the pyttsx3 engine itself,
so it can be unit-tested with fake voice lists — this project's own dev
sandbox has no real SAPI5 voices installed to test against.

Lazily imports pyttsx3 so the rest of AURA keeps working if it isn't
installed, or if the underlying speech engine isn't available on this OS
at all (true in this sandbox, which has no eSpeak/SAPI5).

NOT VERIFIED ON REAL HARDWARE — voice enumeration, female-voice selection,
and actual audible output all need confirmation on the Windows 11 target
machine. See README.md.
"""

import os
from typing import Optional, Sequence

from app.utils.logger import get_logger

logger = get_logger()

DEFAULT_RATE = 175   # words per minute — natural pace, not rushed
DEFAULT_VOLUME = 1.0

# Name/id substrings that suggest a female-sounding English voice. Not
# exhaustive — just the common ones that ship with Windows 10/11 and
# popular third-party SAPI5 packs.
_FEMALE_HINTS = ("female", "zira", "jenny", "aria", "samantha", "hazel")
_ENGLISH_HINTS = ("en-", "en_", "english", "en-us", "en-gb", "en-in")


class VoiceOutputError(Exception):
    """Raised when TTS can't run — caller must show/log a friendly message, never crash."""


def pick_best_voice(voices: Sequence, preferred: Optional[str] = None):
    """
    Pure selection logic, independent of pyttsx3 itself, so it's testable
    with fake voice objects (anything with .id and .name attributes).

    Priority:
      1. `preferred` (from AURA_TTS_VOICE), matched case-insensitively
         against either .name or .id, if it matches anything at all.
      2. Otherwise, the highest-scoring voice: +1 for looking like an
         English voice, +2 for looking like a female voice (so a female
         English voice wins over a female non-English voice or a male
         English voice).
      3. If nothing scores above zero, just the first available voice —
         better to speak in *some* voice than to disable TTS entirely.

    Returns None if `voices` is empty.
    """
    if not voices:
        return None

    if preferred:
        needle = preferred.strip().lower()
        if needle:
            for v in voices:
                haystack = f"{getattr(v, 'name', '') or ''} {getattr(v, 'id', '') or ''}".lower()
                if needle in haystack:
                    return v
            logger.info(f"AURA_TTS_VOICE='{preferred}' didn't match any installed voice; auto-selecting instead.")

    def score(v) -> int:
        haystack = f"{getattr(v, 'name', '') or ''} {getattr(v, 'id', '') or ''}".lower()
        s = 0
        if any(h in haystack for h in _ENGLISH_HINTS):
            s += 1
        if any(h in haystack for h in _FEMALE_HINTS):
            s += 2
        return s

    best = max(voices, key=score)
    return best


class TextToSpeech:
    """
    Lazily initializes the pyttsx3 engine on first use. If initialization
    or speaking fails for any reason (engine unavailable, no voices
    installed, driver error), speak() logs the error and returns quietly
    rather than raising — TTS failing should never take down the rest of
    AURA, since voice output is a convenience, not something other actions
    depend on.
    """

    def __init__(self, rate: Optional[int] = None, volume: Optional[float] = None,
                 preferred_voice: Optional[str] = None):
        self.rate = rate if rate is not None else int(os.environ.get("AURA_TTS_RATE", DEFAULT_RATE))
        self.volume = volume if volume is not None else float(os.environ.get("AURA_TTS_VOLUME", DEFAULT_VOLUME))
        self.preferred_voice = preferred_voice if preferred_voice is not None else os.environ.get("AURA_TTS_VOICE", "")
        self._engine = None
        self._init_failed = False
        self.selected_voice_name: Optional[str] = None

    def _ensure_engine(self) -> bool:
        if self._engine is not None:
            return True
        if self._init_failed:
            return False

        try:
            import pyttsx3
        except ImportError:
            logger.warning("pyttsx3 isn't installed — TTS is disabled. Run: pip install pyttsx3")
            self._init_failed = True
            return False

        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
            self._engine.setProperty("volume", self.volume)

            try:
                voices = self._engine.getProperty("voices") or []
                chosen = pick_best_voice(voices, preferred=self.preferred_voice)
                if chosen is not None:
                    self._engine.setProperty("voice", chosen.id)
                    self.selected_voice_name = getattr(chosen, "name", chosen.id)
                    logger.info(f"TTS voice selected: {self.selected_voice_name}")
            except Exception as e:
                # Voice selection is a nice-to-have — if enumeration fails
                # for any reason, keep the engine's own default voice
                # rather than failing initialization entirely.
                logger.warning(f"Could not select a preferred TTS voice, using engine default: {e}")

            return True
        except Exception as e:
            logger.warning(f"Could not initialize text-to-speech engine: {e}")
            self._init_failed = True
            return False

    def speak(self, text: str) -> None:
        """Speak `text` aloud, blocking until finished. Silently no-ops
        (after logging) if TTS isn't available — never raises, so it's
        always safe to call from the agent's response pipeline."""
        if not text or not text.strip():
            return

        if not self._ensure_engine():
            return

        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as e:
            logger.warning(f"TTS failed while speaking: {e}")

    def is_available(self) -> bool:
        return self._ensure_engine()

    def preload(self) -> None:
        """Initializes the pyttsx3 engine now rather than lazily on first
        speak() — see app/ui/main_window.py's launch(), which calls this
        concurrently with other startup work so the startup greeting
        doesn't pay SAPI5's initialization cost at the exact moment it's
        first needed."""
        self._ensure_engine()

    def list_voices(self) -> list:
        """Returns the raw list of installed voice objects, or [] if TTS
        isn't available. Exposed for the UI's settings panel."""
        if not self._ensure_engine():
            return []
        try:
            return list(self._engine.getProperty("voices") or [])
        except Exception as e:
            logger.warning(f"Could not list TTS voices: {e}")
            return []


def create_tts_engine(preferred: Optional[str] = None):
    """
    Factory used by app/main.py and app/ui/main_window.py. Reads
    AURA_TTS_ENGINE (piper | pyttsx3 | auto) and returns a ready-to-use
    engine object — either a PiperTextToSpeech or a TextToSpeech,
    identical in interface (.speak(text), .is_available()) so the rest
    of the app never needs to know which one it got.

        auto (default): try Piper (only if PIPER_MODEL_PATH is actually
                         set — otherwise there's nothing to try), fall
                         back to pyttsx3 if Piper can't load.
        piper:           Piper only. If it can't load, TTS is disabled
                          entirely rather than silently substituting a
                          different voice/engine than what was asked for.
        pyttsx3:         pyttsx3 only (the original engine).

    This does NOT call preload() — callers should do that themselves,
    ideally as early in startup as possible (see app/main.py), so the
    cost of loading is paid once during startup rather than on the first
    spoken response.
    """
    choice = (preferred or os.environ.get("AURA_TTS_ENGINE", "auto")).strip().lower()

    if choice == "pyttsx3":
        return TextToSpeech()

    if choice == "piper":
        from app.voice.piper_tts import PiperTextToSpeech
        return PiperTextToSpeech()

    # auto
    piper_model = os.environ.get("PIPER_MODEL_PATH", "").strip()
    if piper_model:
        from app.voice.piper_tts import PiperTextToSpeech, PiperUnavailable
        piper = PiperTextToSpeech()
        try:
            piper.preload()
            return piper
        except PiperUnavailable as e:
            logger.info(f"Piper not usable ({e}); falling back to pyttsx3.")

    return TextToSpeech()
