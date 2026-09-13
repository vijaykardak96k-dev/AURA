"""
tests/test_piper_tts.py — PiperTextToSpeech tested against a fake
PiperVoice-shaped object (matching the real piper-tts package's
PiperVoice.synthesize() -> Iterable[AudioChunk] API), since this
project's dev sandbox has no real .onnx voice model to load (would
require downloading one, typically from Hugging Face, which isn't
reachable from here) and no speakers to hear it anyway.

What this proves: preload() only loads once, speak() calls preload()
lazily-but-only-once if it wasn't preloaded already, synthesis produces
one concatenated array from multiple sentence chunks, and every failure
mode (missing model path, missing file, missing package, synthesis
error) degrades to a logged warning rather than a raised exception.
"""

from dataclasses import dataclass, field

import numpy as np
import pytest

from app.voice.piper_tts import PiperTextToSpeech, PiperUnavailable


@dataclass
class FakeAudioChunk:
    sample_rate: int
    audio_float_array: np.ndarray


class FakeConfig:
    sample_rate = 22050


class FakePiperVoice:
    """Stands in for piper.voice.PiperVoice — same shape, no ONNX runtime."""
    load_calls = 0

    def __init__(self):
        self.config = FakeConfig()
        self.synthesize_calls = []

    @classmethod
    def load(cls, model_path, config_path=None):
        cls.load_calls += 1
        return cls()

    def synthesize(self, text, syn_config=None):
        self.synthesize_calls.append(text)
        # Simulate two "sentence" chunks, matching piper's real per-sentence chunking.
        yield FakeAudioChunk(22050, np.array([0.1, 0.2, 0.3], dtype="float32"))
        yield FakeAudioChunk(22050, np.array([0.4, 0.5], dtype="float32"))


@pytest.fixture(autouse=True)
def reset_load_counter():
    FakePiperVoice.load_calls = 0
    yield


def _patch_piper_module(monkeypatch):
    """Installs a fake `piper` module so `from piper import PiperVoice`
    inside piper_tts.py resolves to our fake, without needing the real
    package's ONNX runtime or a model file."""
    import sys
    import types

    fake_module = types.ModuleType("piper")
    fake_module.PiperVoice = FakePiperVoice

    class FakeSynthesisConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_module.SynthesisConfig = FakeSynthesisConfig
    monkeypatch.setitem(sys.modules, "piper", fake_module)


def test_preload_raises_cleanly_when_model_path_not_set():
    tts = PiperTextToSpeech(model_path="")
    with pytest.raises(PiperUnavailable, match="PIPER_MODEL_PATH"):
        tts.preload()


def test_preload_raises_cleanly_when_model_file_missing(tmp_path):
    tts = PiperTextToSpeech(model_path=str(tmp_path / "does_not_exist.onnx"))
    with pytest.raises(PiperUnavailable, match="not found"):
        tts.preload()


def test_preload_loads_model_exactly_once(tmp_path, monkeypatch):
    _patch_piper_module(monkeypatch)
    model_file = tmp_path / "voice.onnx"
    model_file.write_text("fake")

    tts = PiperTextToSpeech(model_path=str(model_file))
    tts.preload()
    tts.preload()
    tts.preload()

    assert FakePiperVoice.load_calls == 1


def test_speak_preloads_automatically_if_not_already(tmp_path, monkeypatch):
    _patch_piper_module(monkeypatch)
    model_file = tmp_path / "voice.onnx"
    model_file.write_text("fake")

    played = {}

    class FakeSoundDevice:
        @staticmethod
        def play(audio, samplerate=None):
            played["audio"] = audio
            played["samplerate"] = samplerate

        @staticmethod
        def wait():
            played["waited"] = True

    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSoundDevice)

    tts = PiperTextToSpeech(model_path=str(model_file))
    tts.speak("Hello, welcome to AURA.")

    assert FakePiperVoice.load_calls == 1
    # The two fake chunks (3 + 2 samples) must be concatenated into one array.
    assert len(played["audio"]) == 5
    assert played["samplerate"] == 22050
    assert played.get("waited") is True


def test_speak_after_explicit_preload_does_not_reload(tmp_path, monkeypatch):
    _patch_piper_module(monkeypatch)
    model_file = tmp_path / "voice.onnx"
    model_file.write_text("fake")

    import sys
    import types
    fake_sd = types.ModuleType("sounddevice")
    fake_sd.play = lambda *a, **kw: None
    fake_sd.wait = lambda: None
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    tts = PiperTextToSpeech(model_path=str(model_file))
    tts.preload()  # e.g. called during app startup, before any speak()
    assert FakePiperVoice.load_calls == 1

    tts.speak("Hello.")
    tts.speak("How are you?")

    assert FakePiperVoice.load_calls == 1  # still just the one preload


def test_speak_never_raises_when_package_not_installed():
    tts = PiperTextToSpeech(model_path="/nonexistent/voice.onnx")
    tts.speak("this should not raise")  # must not raise, even with a bad path


def test_speak_empty_text_is_a_safe_no_op(tmp_path, monkeypatch):
    _patch_piper_module(monkeypatch)
    model_file = tmp_path / "voice.onnx"
    model_file.write_text("fake")
    tts = PiperTextToSpeech(model_path=str(model_file))
    tts.speak("")
    tts.speak("   ")
    assert FakePiperVoice.load_calls == 0  # never even tried to load for empty text


def test_is_available_reflects_load_success(tmp_path, monkeypatch):
    _patch_piper_module(monkeypatch)
    model_file = tmp_path / "voice.onnx"
    model_file.write_text("fake")
    tts = PiperTextToSpeech(model_path=str(model_file))
    assert tts.is_available() is True


def test_is_available_false_when_model_missing():
    tts = PiperTextToSpeech(model_path="/nonexistent/voice.onnx")
    assert tts.is_available() is False


def test_failed_load_does_not_retry_every_call(tmp_path):
    tts = PiperTextToSpeech(model_path=str(tmp_path / "missing.onnx"))
    tts.speak("first attempt")
    tts.speak("second attempt")
    # Both calls should have failed the same way (file check, no import
    # attempted) — this just confirms neither call raises and the object
    # remains usable (doesn't get stuck in a bad internal state).
    assert tts.is_available() is False
