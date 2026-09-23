"""
app/voice/speech_to_text.py

Local speech-to-text via faster-whisper.

AURA has two different microphone modes:

1. SLEEPING
   Short overlapping windows are captured. A cheap RMS gate decides
   whether the window is worth transcribing at all — silence never
   reaches Whisper, which is what keeps CPU near zero while AURA is
   asleep. Windows that do contain sound are transcribed with an initial
   prompt telling Whisper that "AURA" is the assistant's name, and a very
   small conservative ASR repair layer handles common misspellings such
   as "Ora".

2. ACTIVE
   Energy-based voice capture waits for speech, keeps a short pre-roll,
   records until a real silence gap, then transcribes the complete
   command. Recording never runs for a fixed 0.8/1 second window — the
   silence gap is what ends a command.

Two rules matter more than anything else here:

  * Nothing invalid ever reaches Whisper. Every buffer goes through
    app/voice/audio_safety.sanitize_audio() first. See that module for
    why (overflow warnings, NaN matmul, 0xC0000005 crashes).

  * Every blocking capture accepts a `should_continue` callback and
    aborts the moment it returns False. The agent passes its own state
    check in, so an open microphone stream is closed immediately when
    AURA starts speaking instead of recording its own TTS.

No external wake-word model is used.
"""

import os
import time
from typing import Callable, Optional

from app.utils.logger import get_logger
from app.voice.audio_safety import (
    AudioValidationError,
    is_probable_hallucination,
    looks_like_speech,
    rms_energy,
    sanitize_audio,
)

logger = get_logger()

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_RECORD_SECONDS = 5

# base.en is noticeably faster on CPU than 'small' and accurate enough for
# short desktop commands. Override with AURA_WHISPER_MODEL if you prefer
# 'small'/'small.en' and don't mind the extra latency.
WHISPER_MODEL_SIZE = os.environ.get("AURA_WHISPER_MODEL", "base.en").strip() or "base.en"

# ---------------------------------------------------------------------------
# Wake-word listening
# ---------------------------------------------------------------------------

WAKE_WINDOW_SECONDS = 2.2
WAKE_OVERLAP_SECONDS = 0.7

# A sleeping window quieter than this is discarded without ever running
# Whisper's decoder. This is the single biggest CPU saving while asleep.
WAKE_MIN_ENERGY = 0.010

# This prompt is deliberately short.
# It helps Whisper recognize AURA as the assistant's name without asking
# Whisper to make the actual wake/no-wake decision.
WAKE_INITIAL_PROMPT = (
    "AURA is the name of a voice assistant. "
    "The user may say AURA, Hey AURA, or AURA wake up."
)

# Conservative corrections for known Whisper errors observed on this
# machine. These are applied ONLY to the sleeping wake-word transcript.
#
# Do NOT add generic words such as "alright" here. That would create
# dangerous false wake-ups.
_WAKE_ALIASES = {
    "ora": "aura",
    "oura": "aura",
    "aurah": "aura",
    "aurora": "aura",
}

# ---------------------------------------------------------------------------
# Command listening
# ---------------------------------------------------------------------------

COMMAND_PRE_ROLL_SECONDS = 0.45
COMMAND_MAX_WAIT_FOR_SPEECH = 6.0
COMMAND_SILENCE_HANGOVER = 0.80
COMMAND_MAX_RECORD_SECONDS = 15.0

# A captured utterance shorter than this is a cough, a click, or a chair
# creak — never a command. Discarded before Whisper runs.
COMMAND_MIN_SPEECH_SECONDS = 0.35

_VAD_BLOCK_SECONDS = 0.05
_DEFAULT_ENERGY_THRESHOLD = 0.012


class VoiceInputError(Exception):
    """Raised when recording or transcription cannot proceed."""


def _always_continue() -> bool:
    return True


class SpeechToText:
    """
    Shared faster-whisper speech recognizer.

    The Whisper model is loaded once and then reused for all subsequent
    wake-word and command transcription.
    """

    def __init__(
        self,
        model_size: str = WHISPER_MODEL_SIZE,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ):
        self.model_size = model_size
        self.sample_rate = sample_rate
        self._model = None

        # Raw audio tail carried between wake-word windows.
        self._wake_tail = None

        # Counts how many buffers had to be thrown away for being
        # malformed. Useful when diagnosing a flaky microphone.
        self.discarded_buffers = 0

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------

    def _ensure_model_loaded(self):
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise VoiceInputError(
                "Speech recognition isn't installed. Run: "
                "pip install faster-whisper sounddevice numpy"
            ) from e

        try:
            t0 = time.monotonic()
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
            )

            logger.info(
                f"Loaded faster-whisper model "
                f"'{self.model_size}' (CPU, int8) in "
                f"{time.monotonic() - t0:.2f}s."
            )

        except Exception as e:
            raise VoiceInputError(
                f"Could not load the speech recognition model: {e}"
            ) from e

    def preload(self) -> None:
        """Load Whisper before the first real microphone interaction."""
        self._ensure_model_loaded()

    # ------------------------------------------------------------------
    # Audio imports
    # ------------------------------------------------------------------

    @staticmethod
    def _import_audio_libs():
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as e:
            raise VoiceInputError(
                "Microphone recording isn't installed. Run: "
                "pip install sounddevice numpy"
            ) from e
        except OSError as e:
            raise VoiceInputError(
                "The audio system (PortAudio) isn't available. "
                "Try reinstalling sounddevice with: "
                "pip install --force-reinstall sounddevice"
            ) from e

        return np, sd

    # ------------------------------------------------------------------
    # Legacy fixed-window recording
    # ------------------------------------------------------------------

    def record_audio(self, seconds: int = DEFAULT_RECORD_SECONDS):
        """
        Record a fixed amount of microphone audio.
        Kept for compatibility with older callers.
        """
        np, sd = self._import_audio_libs()

        try:
            audio = sd.rec(
                int(seconds * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
            )

            sd.wait()

            return audio.flatten()

        except Exception as e:
            raise VoiceInputError(
                "I couldn't access the microphone. Check that it's "
                "connected and microphone permission is granted."
            ) from e

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio,
        *,
        wake_mode: bool = False,
    ) -> str:
        """
        Transcribe audio with faster-whisper.

        Returns "" — never raises — for anything unusable: a malformed
        buffer, a silent buffer, a Whisper failure, or one of Whisper's
        well-known phantom transcripts. "Heard nothing" is always a safe
        answer; a crash is not. The caller's job is simply to keep
        listening when it gets "" back.

        wake_mode=True:
            Uses the AURA-specific initial prompt and stronger anti-
            hallucination settings for short wake-word windows.
        """
        try:
            np, _sd = self._import_audio_libs()
        except VoiceInputError:
            return ""

        try:
            audio = sanitize_audio(np, audio)
        except AudioValidationError as e:
            self.discarded_buffers += 1
            logger.warning(
                f"Discarded malformed audio buffer ({e}); still listening."
            )
            return ""
        except Exception as e:
            self.discarded_buffers += 1
            logger.warning(f"Could not sanitize audio buffer: {e}")
            return ""

        try:
            self._ensure_model_loaded()
        except VoiceInputError as e:
            logger.error(f"Whisper unavailable: {e}")
            return ""

        t0 = time.monotonic()

        try:
            kwargs = {
                "language": "en",
                "beam_size": 1,
                "temperature": 0,
                "condition_on_previous_text": False,
                "vad_filter": True,
                "no_speech_threshold": 0.60,
                "log_prob_threshold": -1.0,
                "compression_ratio_threshold": 2.4,
            }

            if wake_mode:
                kwargs["initial_prompt"] = WAKE_INITIAL_PROMPT

            segments, _info = self._model.transcribe(
                audio,
                **kwargs,
            )

            text = " ".join(
                seg.text.strip()
                for seg in segments
                if seg.text and seg.text.strip()
            ).strip()

        except Exception as e:
            # A native CTranslate2 failure must not propagate — it used to
            # take the whole backend down with it.
            logger.warning(f"Transcription failed, ignoring this audio: {e}")
            return ""

        elapsed = time.monotonic() - t0

        if is_probable_hallucination(text, wake_mode=wake_mode):
            logger.info(
                f"Transcribed: {text!r} in {elapsed:.2f}s "
                "-> discarded (no real speech)."
            )
            return ""

        logger.info(f"Transcribed: {text!r} in {elapsed:.2f}s")

        return text

    def listen_and_transcribe(
        self,
        seconds: int = DEFAULT_RECORD_SECONDS,
    ) -> str:
        """Legacy fixed-window record + transcription."""
        audio = self.record_audio(seconds)
        return self.transcribe(audio)

    # ------------------------------------------------------------------
    # Wake-word ASR repair
    # ------------------------------------------------------------------

    @staticmethod
    def _repair_wake_transcript(text: str) -> str:
        """
        Conservatively repair common Whisper spellings of AURA.

        IMPORTANT:
        This is NOT wake-word detection.

        wake_word.py still performs the actual hard gate.

        We only convert very specific ASR variants:
            Ora       -> aura
            Oura      -> aura
            Aurah     -> aura

        We intentionally do NOT convert words such as:
            alright
            all right
            aura-related arbitrary phrases

        because that could cause false wake-ups.
        """
        if not text:
            return ""

        words = text.split()
        repaired = []

        for word in words:
            # Preserve punctuation around the token.
            leading = ""
            trailing = ""

            while word and not word[0].isalnum():
                leading += word[0]
                word = word[1:]

            while word and not word[-1].isalnum():
                trailing = word[-1] + trailing
                word = word[:-1]

            replacement = _WAKE_ALIASES.get(word.lower())

            if replacement:
                word = replacement

            repaired.append(f"{leading}{word}{trailing}")

        result = " ".join(repaired).strip()

        if result != text.strip():
            logger.info(
                f'Wake ASR correction: "{text}" -> "{result}"'
            )

        return result

    # ------------------------------------------------------------------
    # Wake-word listening
    # ------------------------------------------------------------------

    def listen_for_wake_word(
        self,
        window_seconds: float = WAKE_WINDOW_SECONDS,
        overlap_seconds: float = WAKE_OVERLAP_SECONDS,
        should_continue: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        Capture a short overlapping audio window.

        The previous window's tail is prepended to the new recording so
        the wake phrase is less likely to be split across boundaries.

        Windows that are essentially silent are discarded WITHOUT running
        Whisper, so a sleeping AURA costs almost no CPU.

        Returns the raw-ish transcript after conservative ASR repair.
        The actual wake-word decision remains in wake_word.py.
        """
        should_continue = should_continue or _always_continue

        np, sd = self._import_audio_libs()

        try:
            fresh = sd.rec(
                int(window_seconds * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
            )

            sd.wait()

            fresh = fresh.flatten()

        except Exception as e:
            raise VoiceInputError(
                "I couldn't access the microphone. Check that it's "
                "connected and microphone permission is granted."
            ) from e

        if not should_continue():
            # AURA left SLEEPING while this window was recording.
            self._wake_tail = None
            return ""

        try:
            fresh = sanitize_audio(np, fresh)
        except Exception as e:
            self.discarded_buffers += 1
            logger.warning(f"Discarded malformed wake window: {e}")
            self._wake_tail = None
            return ""

        # --------------------------------------------------------------
        # Combine previous tail + fresh audio
        # --------------------------------------------------------------

        if self._wake_tail is not None and len(self._wake_tail) > 0:
            combined = np.concatenate(
                [self._wake_tail, fresh]
            )
        else:
            combined = fresh

        # Save tail for the next overlapping window.
        overlap_samples = int(
            overlap_seconds * self.sample_rate
        )

        if len(fresh) > overlap_samples:
            self._wake_tail = fresh[-overlap_samples:].copy()
        else:
            self._wake_tail = fresh.copy()

        # --------------------------------------------------------------
        # Cheap energy gate: silence never reaches Whisper.
        # --------------------------------------------------------------

        if rms_energy(np, combined) < WAKE_MIN_ENERGY:
            return ""

        # --------------------------------------------------------------
        # Whisper wake transcription
        # --------------------------------------------------------------

        text = self.transcribe(
            combined,
            wake_mode=True,
        )

        if not text:
            return ""

        # Conservative correction of known AURA ASR mistakes.
        text = self._repair_wake_transcript(text)

        logger.info(
            f'Whisper heard (sleeping): "{text}"'
        )

        return text

    def reset_wake_buffer(self) -> None:
        """
        Clear overlapping wake audio when leaving SLEEPING.

        Prevents audio from before a wake event from leaking into the
        next sleeping session.
        """
        self._wake_tail = None

    # ------------------------------------------------------------------
    # ACTIVE command listening
    # ------------------------------------------------------------------

    def listen_until_silence(
        self,
        max_wait_for_speech: float = COMMAND_MAX_WAIT_FOR_SPEECH,
        silence_hangover: float = COMMAND_SILENCE_HANGOVER,
        max_record_seconds: float = COMMAND_MAX_RECORD_SECONDS,
        pre_roll_seconds: float = COMMAND_PRE_ROLL_SECONDS,
        energy_threshold: Optional[float] = None,
        initial_discard_seconds: float = 0.0,
        min_speech_seconds: float = COMMAND_MIN_SPEECH_SECONDS,
        on_speech_start: Optional[Callable[[], None]] = None,
        should_continue: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        Wait for speech, record until silence, then transcribe.

        Command boundaries come from the audio itself, never from a fixed
        timer:

            no speech yet        -> keep waiting (up to max_wait_for_speech)
            energy crosses gate  -> command started (on_speech_start fires)
            energy stays up      -> keep recording
            silence_hangover of
              continuous quiet   -> command finished, transcribe

        A short pre-roll prevents clipping the first syllable.

        `should_continue`, if given, is polled on every 50 ms block. The
        agent passes its own "am I still listening?" check, so the stream
        closes immediately when AURA starts speaking rather than recording
        its own TTS through the speakers.

        Returns "" for: no speech, aborted capture, too-short capture,
        noise that doesn't look like speech, or an unusable buffer.
        """
        should_continue = should_continue or _always_continue

        if energy_threshold is None:
            try:
                energy_threshold = float(
                    os.environ.get(
                        "AURA_VAD_ENERGY_THRESHOLD",
                        _DEFAULT_ENERGY_THRESHOLD,
                    )
                )
            except (TypeError, ValueError):
                energy_threshold = _DEFAULT_ENERGY_THRESHOLD

        np, sd = self._import_audio_libs()

        block_size = max(
            1,
            int(_VAD_BLOCK_SECONDS * self.sample_rate),
        )

        pre_roll_blocks = max(
            1,
            int(pre_roll_seconds / _VAD_BLOCK_SECONDS),
        )

        pre_roll_buffer = []
        captured = []

        speech_started = False
        silence_run = 0.0
        elapsed_waiting = 0.0
        elapsed_recording = 0.0
        aborted = False

        logger.info("Listening for command...")

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
            ) as stream:

                # Flush the microphone's existing audio before listening for
                # the next command. This is especially important immediately
                # after AURA's TTS, where the speaker tail can still be heard
                # by the microphone.
                discard_remaining = max(
                    0.0,
                    float(initial_discard_seconds),
                )

                if discard_remaining > 0.0:
                    discard_blocks = max(
                        1,
                        int(discard_remaining / _VAD_BLOCK_SECONDS),
                    )

                    for _ in range(discard_blocks):
                        if not should_continue():
                            return ""
                        stream.read(block_size)

                while True:
                    if not should_continue():
                        aborted = True
                        logger.info(
                            "Microphone capture aborted "
                            "(AURA is no longer listening)."
                        )
                        break

                    block, _overflowed = stream.read(block_size)

                    block = np.asarray(block).reshape(-1)

                    energy = rms_energy(np, block)

                    # --------------------------------------------------
                    # Waiting for speech
                    # --------------------------------------------------

                    if not speech_started:

                        pre_roll_buffer.append(block)

                        if len(pre_roll_buffer) > pre_roll_blocks:
                            pre_roll_buffer.pop(0)

                        if energy >= energy_threshold:

                            speech_started = True

                            logger.info("Speech detected")

                            if on_speech_start is not None:
                                try:
                                    on_speech_start()
                                except Exception:
                                    logger.warning(
                                        "on_speech_start callback raised",
                                        exc_info=True,
                                    )

                            captured.extend(pre_roll_buffer)
                            captured.append(block)

                        else:
                            elapsed_waiting += _VAD_BLOCK_SECONDS

                            if elapsed_waiting >= max_wait_for_speech:
                                return ""

                        continue

                    # --------------------------------------------------
                    # Already speaking
                    # --------------------------------------------------

                    captured.append(block)

                    elapsed_recording += _VAD_BLOCK_SECONDS

                    if energy < energy_threshold:

                        silence_run += _VAD_BLOCK_SECONDS

                        if silence_run >= silence_hangover:
                            logger.info("Silence detected — command ended")
                            break

                    else:
                        silence_run = 0.0

                    if elapsed_recording >= max_record_seconds:
                        logger.info("Maximum recording time reached")
                        break

        except Exception as e:
            raise VoiceInputError(
                "I couldn't access the microphone. Check that it's "
                "connected and microphone permission is granted."
            ) from e

        if aborted or not captured:
            return ""

        # Everything past the silence hangover is trailing quiet — keep a
        # little of it so the last word isn't clipped, drop the rest.
        keep_tail_blocks = max(1, int(0.25 / _VAD_BLOCK_SECONDS))
        trailing_silence_blocks = int(silence_run / _VAD_BLOCK_SECONDS)

        if trailing_silence_blocks > keep_tail_blocks:
            drop = trailing_silence_blocks - keep_tail_blocks
            if 0 < drop < len(captured):
                captured = captured[:-drop]

        try:
            audio = np.concatenate(captured)
        except Exception as e:
            self.discarded_buffers += 1
            logger.warning(f"Could not assemble captured audio: {e}")
            return ""

        # ------------------------------------------------------------------
        # Reject anything that isn't plausibly a spoken command BEFORE
        # paying for a Whisper decode.
        # ------------------------------------------------------------------

        if not looks_like_speech(
            np,
            audio,
            self.sample_rate,
            min_seconds=min_speech_seconds,
            energy_threshold=energy_threshold * 0.5,
        ):
            logger.info(
                "Captured audio was too short/quiet to be a command — ignoring."
            )
            return ""

        if not should_continue():
            logger.info(
                "Discarding captured audio — AURA is no longer listening."
            )
            return ""

        return self.transcribe(audio, wake_mode=False)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_SHARED_STT: Optional[SpeechToText] = None


def create_stt_engine(model_size: Optional[str] = None) -> SpeechToText:
    """
    Return the process-wide SpeechToText instance.

    A single shared instance matters: each one owns a faster-whisper
    model, and loading a second copy would double RAM for no benefit.
    """
    global _SHARED_STT

    if _SHARED_STT is None:
        _SHARED_STT = SpeechToText(
            model_size=model_size or WHISPER_MODEL_SIZE
        )

    return _SHARED_STT
