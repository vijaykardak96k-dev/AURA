"""
tests/test_wake_word.py — pure text-matching logic for wake-word
detection, with zero dependency on audio, threading, or the agent. See
tests/test_agent.py for the same variants exercised end-to-end through
the actual voice loop.
"""

import pytest

from app.voice.wake_word import build_wake_pattern, detect_wake_word, normalize_transcript


# -------------------------------------------------------------- normalization

@pytest.mark.parametrize("raw,expected", [
    ("Aura", "aura"),
    ("AURA.", "aura"),
    ("  aura   open   chrome  ", "aura open chrome"),
    ("Hey, AURA!", "hey aura"),
    ("aura's", "aura's"),
    ("", ""),
    (None, ""),
])
def test_normalize_transcript(raw, expected):
    assert normalize_transcript(raw) == expected


# --------------------------------------------------------- required variants
# Every phrasing explicitly required by the spec, plus casing/punctuation
# variations Whisper is known to produce for the same underlying speech.

@pytest.mark.parametrize("heard", [
    "AURA", "Aura", "aura", "AURA.", "aura,", "aura!",
    "hey aura", "Hey AURA", "HEY AURA",
    "aura wake up", "Aura wake up.", "AURA WAKE UP",
    "hey aura wake up", "Hey Aura wake up.",
])
def test_bare_wake_phrase_variants_all_detected_with_empty_remainder(heard):
    result = detect_wake_word(heard)
    assert result.detected is True, f"{heard!r} should have been detected"
    assert result.remainder == "", f"{heard!r} should have no remainder, got {result.remainder!r}"


@pytest.mark.parametrize("heard,expected_remainder", [
    ("AURA open Chrome", "open chrome"),
    ("aura, open chrome", "open chrome"),
    ("hey aura open Chrome", "open chrome"),
    ("Hey AURA, open Chrome.", "open chrome"),
    ("aura create a folder called Test", "create a folder called test"),
    ("hey aura what time is it", "what time is it"),
    ("aura wake up open chrome", "open chrome"),
])
def test_wake_plus_command_strips_wake_phrase_only(heard, expected_remainder):
    result = detect_wake_word(heard)
    assert result.detected is True
    assert result.remainder == expected_remainder
    assert "aura" not in result.remainder, "the wake word itself must never appear in the remainder"


# ------------------------------------------------------------- false positives

@pytest.mark.parametrize("heard", [
    "what does aura mean",
    "aurangabad is a city",
    "i saw an aurora last night",
    "open chrome",
    "hey there, how are you",
    "",
    "   ",
])
def test_non_wake_phrases_never_detected(heard):
    result = detect_wake_word(heard)
    assert result.detected is False
    assert result.remainder == ""


def test_wake_word_is_start_anchored_not_a_substring_search():
    """A wake word mentioned mid-sentence is NOT the same as being
    addressed — only a match at the very start of the utterance counts."""
    result = detect_wake_word("chrome aura open")
    assert result.detected is False


def test_custom_wake_name_can_be_configured():
    result = detect_wake_word("computer open chrome", wake_name="computer")
    assert result.detected is True
    assert result.remainder == "open chrome"

    # The default "aura" pattern must not fire for a differently-configured name.
    result2 = detect_wake_word("aura open chrome", wake_name="computer")
    assert result2.detected is False


def test_build_wake_pattern_is_word_boundary_safe():
    pattern = build_wake_pattern("aura")
    assert pattern.match("aura open chrome")
    assert not pattern.match("aurangabad")
    assert not pattern.match("auraaditya")
