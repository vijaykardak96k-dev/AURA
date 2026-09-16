"""
app/voice/speech_to_text.py

Local speech-to-text via faster-whisper.

AURA has two different microphone modes:

1. SLEEPING
   Short overlapping windows are continuously transcribed only to detect
   the wake word. Whisper receives an initial prompt telling it that
   "AURA" is the assistant's name. A very small conservative ASR repair
   layer handles common Whisper spellings such as "Ora".

2. ACTIVE
   Energy-based voice capture waits for speech, keeps a short pre-roll,
   records until silence, then transcribes the complete command.

No external wake-word model is used.
"""

import os
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger()

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_RECORD_SECONDS = 5
WHISPER_MODEL_SIZE = "small"

# ---------------------------------------------------------------------------
# Wake-word listening
# ---------------------------------------------------------------------------

WAKE_WINDOW_SECONDS = 2.8
WAKE_OVERLAP_SECONDS = 1.0

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
}

# ---------------------------------------------------------------------------
# Command listening
# ---------------------------------------------------------------------------

COMMAND_PRE_ROLL_SECONDS = 0.30
COMMAND_MAX_WAIT_FOR_SPEECH = 8.0
COMMAND_SILENCE_HANGOVER = 0.90
COMMAND_MAX_RECORD_SECONDS = 15.0

_VAD_BLOCK_SECONDS = 0.05
_DEFAULT_ENERGY_THRESHOLD = 0.012


class VoiceInputError(Exception):
    """Raised when recording or transcription cannot proceed."""


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
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
            )

            logger.info(
                f"Loaded faster-whisper model "
                f"'{self.model_size}' (CPU, int8)."
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
            logger.info(
                f"Recording {seconds}s of audio at "
                f"{self.sample_rate} Hz..."
            )

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

        wake_mode=True:
            Uses the AURA-specific initial prompt and stronger anti-
            hallucination settings for short wake-word windows.

        wake_mode=False:
            Used for normal commands.
        """
        self._ensure_model_loaded()

        try:
            kwargs = {
                "language": "en",
                "beam_size": 3,
                "best_of": 3,
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

            logger.info(f"Transcribed: {text!r}")

            return text

        except Exception as e:
            raise VoiceInputError(
                f"Transcription failed: {e}"
            ) from e

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
    ) -> str:
        """
        Capture a short overlapping audio window.

        The previous window's tail is prepended to the new recording so
        the wake phrase is less likely to be split across boundaries.

        Returns the raw-ish transcript after conservative ASR repair.
        The actual wake-word decision remains in wake_word.py.
        """
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
        # Whisper wake transcription
        # --------------------------------------------------------------

        text = self.transcribe(
            combined,
            wake_mode=True,
        )

        # Conservative correction of known AURA ASR mistakes.
        text = self._repair_wake_transcript(text)

        logger.info(
            f'Whisper heard: "{text}"'
        )

        return text

    def reset_wake_buffer(self) -> None:
        """
        Clear overlapping wake audio when leaving SLEEPING.

        Prevents audio from before a wake event from leaking into the
        next sleeping session.
        """
        self._wake_tail = None


    def listen_for_interrupt(self, max_record_seconds: float = 1.0) -> str:
        """Capture a short audio window used only while AURA is speaking.

        This deliberately does not use the normal VAD command path: the
        interrupt listener must return quickly and should only be interpreted
        by the agent for explicit words such as stop/mute.
        """
        seconds = max(0.6, min(1.5, float(max_record_seconds)))
        audio = self.record_audio(seconds)
        return self.transcribe(audio, wake_mode=False).strip()

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
    ) -> str:
        """
        Wait for speech, record until silence, then transcribe.

        A short pre-roll prevents clipping the first syllable of a
        command.
        """
        if energy_threshold is None:
            energy_threshold = float(
                os.environ.get(
                    "AURA_VAD_ENERGY_THRESHOLD",
                    _DEFAULT_ENERGY_THRESHOLD,
                )
            )

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

        logger.info(
            "Listening for command..."
        )

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

                while discard_remaining > 0.0:
                    discard_blocks = max(
                        1,
                        int(
                            discard_remaining
                            / _VAD_BLOCK_SECONDS
                        ),
                    )

                    for _ in range(discard_blocks):
                        stream.read(block_size)

                    discard_remaining = 0.0

                while True:
                    block, _overflowed = stream.read(block_size)

                    block = block.flatten()

                    energy = (
                        float(
                            np.sqrt(
                                np.mean(
                                    np.square(block)
                                )
                            )
                        )
                        if len(block)
                        else 0.0
                    )

                    # --------------------------------------------------
                    # Waiting for speech
                    # --------------------------------------------------

                    if not speech_started:

                        pre_roll_buffer.append(block)

                        if len(pre_roll_buffer) > pre_roll_blocks:
                            pre_roll_buffer.pop(0)

                        if energy >= energy_threshold:

                            speech_started = True

                            logger.info(
                                "Speech detected"
                            )

                            captured.extend(
                                pre_roll_buffer
                            )

                            captured.append(block)

                        else:
                            elapsed_waiting += (
                                _VAD_BLOCK_SECONDS
                            )

                            if (
                                elapsed_waiting
                                >= max_wait_for_speech
                            ):
                                return ""

                        continue

                    # --------------------------------------------------
                    # Already speaking
                    # --------------------------------------------------

                    captured.append(block)

                    elapsed_recording += (
                        _VAD_BLOCK_SECONDS
                    )

                    if energy < energy_threshold:

                        silence_run += (
                            _VAD_BLOCK_SECONDS
                        )

                        if (
                            silence_run
                            >= silence_hangover
                        ):
                            logger.info(
                                "Silence detected"
                            )
                            break

                    else:
                        silence_run = 0.0

                    if (
                        elapsed_recording
                        >= max_record_seconds
                    ):
                        logger.info(
                            "Maximum recording time reached"
                        )
                        break

        except Exception as e:
            raise VoiceInputError(
                "I couldn't access the microphone. Check that it's "
                "connected and microphone permission is granted."
            ) from e

        if not captured:
            return ""

        audio = np.concatenate(captured)

        text = self.transcribe(
            audio,
            wake_mode=False,
        )

        logger.info(
            f'Command transcription: "{text}"'
        )

        return text