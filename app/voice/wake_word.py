"""
app/voice/wake_word.py

Pure text-processing wake-word detection — deliberately has NOTHING to do
with audio, threading, or the agent's state machine. This is the actual
fix for the "AURA falls through to Gemini" bug: wake-word detection must
happen as a hard gate BEFORE any transcript is ever handed to
intent_parser/handle_input, and that gate needs to be simple, pure, and
easy to verify in isolation — which is exactly what handing transcript
classification to the AI prompt (asking Gemini to "recognize wake
words") made unreliable in the first place. Gemini being asked to interpret "Aura" naturally
answers it conversationally instead of treating it as a control signal
— that's not a bug in Gemini, it's a category error in *where* wake-word
detection was being attempted.

detect_wake_word() is called by the voice loop (app/core/agent.py) only
while the agent is SLEEPING, and its result decides everything: if no
wake word is found, the transcript is discarded outright — it NEVER
reaches parse_intent()/Gemini/Ollama. There is no code path from
SLEEPING to the AI pipeline that skips this function.
"""

import re
from typing import NamedTuple

# The wake word itself is derived from AURA_NAME so it stays consistent
# with the assistant's configured identity (app/brain/identity.py) rather
# than being a second, independently-hardcoded name.
DEFAULT_WAKE_NAME = "aura"


class WakeWordResult(NamedTuple):
    detected: bool
    remainder: str  # command text after the wake phrase, "" if none


def normalize_transcript(text: str) -> str:
    """
    Collapses the many equivalent forms Whisper can produce for the same
    utterance ("Aura.", "AURA", "aura,", extra   spaces) into one
    canonical lowercase form with punctuation removed, so detection never
    depends on exact casing or punctuation.
    """
    if not text:
        return ""
    lowered = text.lower()
    # Strip punctuation (keep word characters, spaces, and apostrophes
    # for contractions elsewhere in the remainder command).
    stripped = re.sub(r"[^\w\s']", " ", lowered)
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed


def build_wake_pattern(wake_name: str = DEFAULT_WAKE_NAME) -> re.Pattern:
    """
    Matches, at the START of a normalized transcript:
        (hey )? <wake_name> (wake up)?
    with a word boundary after <wake_name> so "auraaditya" or "aurangabad"
    can never accidentally match — only the exact wake token, optionally
    preceded by "hey" and optionally followed by "wake up".

    This covers every variant in the spec by construction, rather than by
    an ever-growing literal list:
        aura | hey aura | aura wake up | hey aura wake up
        + any punctuation/casing Whisper produces, since matching happens
          AFTER normalize_transcript() has already stripped that out.
    """
    name = re.escape(wake_name.strip().lower())
    return re.compile(rf"^(?:hey\s+)?{name}\b(?:\s+wake\s+up\b)?[\s]*")


_DEFAULT_PATTERN = build_wake_pattern(DEFAULT_WAKE_NAME)


def detect_wake_word(raw_text: str, wake_name: str = DEFAULT_WAKE_NAME) -> WakeWordResult:
    """
    THE hard gate. Returns detected=False for anything that doesn't start
    with the wake phrase — including a transcript that merely *mentions*
    "aura" mid-sentence ("what does aura mean") — wake-word activation is
    intentionally start-anchored, matching how a real wake word behaves.

    On a match, `remainder` is whatever follows the wake phrase (leading
    punctuation/whitespace stripped), which is what gets handed to the
    normal command pipeline — the wake phrase itself is never included.
    """
    normalized = normalize_transcript(raw_text)
    if not normalized:
        return WakeWordResult(False, "")

    pattern = _DEFAULT_PATTERN if wake_name == DEFAULT_WAKE_NAME else build_wake_pattern(wake_name)
    match = pattern.match(normalized)
    if not match:
        return WakeWordResult(False, "")

    remainder = normalized[match.end():].strip(" ,.!?")
    return WakeWordResult(True, remainder)
