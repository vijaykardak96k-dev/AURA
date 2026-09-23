"""
tests/test_tts_voice_selection.py — pick_best_voice() pure logic, tested
with fake voice objects since this sandbox has no real SAPI5 voices to
enumerate. This is exactly why pick_best_voice() was deliberately kept
separate from the pyttsx3 engine.
"""

from collections import namedtuple

from app.voice.text_to_speech import pick_best_voice

FakeVoice = namedtuple("FakeVoice", ["id", "name"])


def test_prefers_female_english_voice_over_male_english():
    voices = [
        FakeVoice("HKEY\\...\\TTS_MS_EN-US_DAVID_11.0", "Microsoft David Desktop - English (United States)"),
        FakeVoice("HKEY\\...\\TTS_MS_EN-US_ZIRA_11.0", "Microsoft Zira Desktop - English (United States)"),
    ]
    chosen = pick_best_voice(voices)
    assert "zira" in chosen.name.lower()


def test_prefers_english_female_over_non_english_female():
    voices = [
        FakeVoice("id1", "Microsoft Hortense - French (France)"),  # female-ish name, not English
        FakeVoice("id2", "Microsoft Hazel Desktop - English (Great Britain)"),
    ]
    chosen = pick_best_voice(voices)
    assert "hazel" in chosen.name.lower()


def test_falls_back_to_first_voice_when_nothing_scores():
    voices = [
        FakeVoice("id1", "Some Unrelated Voice Pack"),
        FakeVoice("id2", "Another Generic Voice"),
    ]
    chosen = pick_best_voice(voices)
    assert chosen in voices  # doesn't crash, returns *something*


def test_empty_voice_list_returns_none():
    assert pick_best_voice([]) is None


def test_preferred_voice_override_matches_by_name():
    voices = [
        FakeVoice("id1", "Microsoft David Desktop - English (United States)"),
        FakeVoice("id2", "Microsoft Zira Desktop - English (United States)"),
    ]
    chosen = pick_best_voice(voices, preferred="david")
    assert "david" in chosen.name.lower()


def test_preferred_voice_override_matches_by_id():
    voices = [
        FakeVoice("com.apple.speech.synthesis.voice.samantha", "Samantha"),
        FakeVoice("com.apple.speech.synthesis.voice.alex", "Alex"),
    ]
    chosen = pick_best_voice(voices, preferred="alex")
    assert chosen.name == "Alex"


def test_preferred_voice_with_no_match_falls_back_to_scoring():
    voices = [
        FakeVoice("id1", "Microsoft David Desktop - English (United States)"),
        FakeVoice("id2", "Microsoft Zira Desktop - English (United States)"),
    ]
    chosen = pick_best_voice(voices, preferred="a-voice-that-does-not-exist")
    assert "zira" in chosen.name.lower()  # scoring fallback still prefers female


def test_recognizes_jenny_aria_samantha_as_female_hints():
    for name in ["Microsoft Jenny", "Microsoft Aria Online", "Samantha"]:
        voices = [
            FakeVoice("id_male", "Microsoft Mark - English (United States)"),
            FakeVoice("id_female", name),
        ]
        chosen = pick_best_voice(voices)
        assert chosen.name == name, f"Expected {name!r} to win over a generic male English voice"
