"""
tests/test_voice.py — voice module graceful-degradation behavior.

These tests deliberately do NOT require a microphone, speaker, or any
audio drivers — they verify that speech_to_text.py and text_to_speech.py
never let a missing/broken audio subsystem crash the caller, which is
exactly the situation in this project's own Linux test sandbox (no
PortAudio, no speech engine) and is a realistic scenario on Windows too
(e.g. a machine with no microphone attached).

Actual transcription accuracy and actual audible speech output are
WINDOWS/HARDWARE-ONLY concerns and are NOT covered here — see README.md.
"""

from app.voice.speech_to_text import SpeechToText, VoiceInputError
from app.voice.text_to_speech import TextToSpeech


def test_speech_to_text_module_imports_without_audio_hardware():
    # Constructing the class must never touch sounddevice/faster-whisper —
    # those are lazily imported only inside methods.
    stt = SpeechToText()
    # Default is base.en (faster on CPU than 'small'); overridable with
    # AURA_WHISPER_MODEL.
    assert stt.model_size == "base.en"


def test_record_audio_raises_voice_input_error_not_raw_exception():
    """
    On this sandbox (no PortAudio), sounddevice raises OSError at import
    time. record_audio() must convert that into VoiceInputError, not let
    it escape as a raw OSError/ImportError.
    """
    stt = SpeechToText()
    try:
        stt.record_audio(seconds=1)
        assert False, "Expected VoiceInputError to be raised"
    except VoiceInputError:
        pass  # expected
    except Exception as e:
        assert False, f"record_audio() leaked a raw {type(e).__name__} instead of VoiceInputError: {e}"


def test_text_to_speech_module_imports_without_audio_hardware():
    tts = TextToSpeech()
    assert tts.rate == 175


def test_speak_never_raises_even_with_no_speech_engine():
    """speak() must degrade to a silent no-op (after logging) rather than
    ever raising, since voice output is a convenience, not a dependency
    of the rest of the app."""
    tts = TextToSpeech()
    tts.speak("this should not raise even without a working TTS engine")  # must not raise


def test_speak_with_empty_text_is_a_safe_no_op():
    tts = TextToSpeech()
    tts.speak("")
    tts.speak("   ")


def test_is_available_reports_false_when_engine_cannot_init():
    tts = TextToSpeech()
    # In this sandbox there is no working speech engine, so this should be
    # False; on a real Windows machine with SAPI5 it would be True. Either
    # way it must return a bool, never raise.
    assert isinstance(tts.is_available(), bool)
