"""
app/brain/emotion.py

Picks which of AURA's fixed emotion labels a reply should be spoken with.
This module never touches audio — it only returns a string from EMOTIONS.
app/voice/prosody.py turns that string into actual synthesis parameters.

Selection order (see AURA v3 master prompt, Task 2):

  1. If the AI provider's reply JSON already names a valid emotion, use it
     as-is (select_emotion(..., ai_emotion=...)). Not currently wired to
     the provider JSON schema — see NOTE at the bottom of this file.
  2. Otherwise, rule-based from the reply text, the user's message, and
     the outcome of whatever action just ran.
  3. Anything unrecognized or missing falls back to "neutral". This
     function never raises and never returns a value outside EMOTIONS.

Pure and deterministic (no randomness, no I/O) so it's unit-testable
without any audio hardware — see tests/test_emotion.py.
"""

from typing import Optional

EMOTIONS = (
    "neutral", "warm", "happy", "excited", "playful", "calm",
    "concerned", "serious", "empathetic", "confident",
)

_DEFAULT = "neutral"

# Keyword hints, checked in order — first match wins. Kept short and
# obvious on purpose; this is prosody shading, not sentiment analysis.
_USER_HINTS = (
    ("empathetic", ("terrible day", "bad day", "rough day", "i'm sad", "im sad",
                     "feeling down", "not okay", "not ok", "stressed", "exhausted",
                     "i failed", "i'm tired", "im tired")),
    ("playful", ("unpaid employee", "you're terrible", "youre terrible", "you suck",
                 "bet you can't", "bet you cant", "blah blah", "you're useless",
                 "youre useless", "lol", "haha")),
    ("warm", ("hey aura", "hi aura", "good morning", "good evening", "good night",
              "thank you", "thanks")),
    ("excited", ("amazing", "awesome", "no way", "finally fixed", "i did it", "we did it")),
)

_REPLY_HINTS = (
    ("serious", ("shut down", "shutdown", "restart the computer", "delete", "format",
                 "confirm", "are you sure", "kill ")),
    ("concerned", ("i couldn't", "i can't", "sorry", "failed", "error", "went wrong",
                    "i don't currently have access", "unable to")),
    ("confident", ("done.", "done!", "handled", "all set", "sorted", "finished.")),
    ("empathetic", ("that sounds rough", "want to talk about it", "i'm here for you",
                     "here if you need me")),
    ("playful", ("harsh", "nice try", "give me another chance", "cheeky")),
    ("excited", ("that's great", "thats great", "nice!", "love that")),
)

_OUTCOME_MAP = {
    "success": "confident",
    "error": "concerned",
    "confirm": "serious",
    "greeting": "warm",
}


def _contains_any(haystack: str, needles) -> bool:
    return any(n in haystack for n in needles)


def select_emotion(
    reply_text: str = "",
    user_text: str = "",
    ai_emotion: Optional[str] = None,
    outcome: Optional[str] = None,
) -> str:
    """
    Returns one label from EMOTIONS. Never raises.

    reply_text  — what AURA is about to say (primary signal).
    user_text   — what the user just said, if available (secondary signal).
    ai_emotion  — an emotion the AI provider itself suggested, if the
                  reply JSON carried one; takes priority when valid.
    outcome     — one of "success" / "error" / "confirm" / "greeting" if
                  the caller knows why this reply is happening; used only
                  when neither text gives a clearer signal.
    """
    if ai_emotion:
        candidate = str(ai_emotion).strip().lower()
        if candidate in EMOTIONS:
            return candidate

    reply_lower = (reply_text or "").strip().lower()
    user_lower = (user_text or "").strip().lower()

    for emotion, needles in _USER_HINTS:
        if _contains_any(user_lower, needles):
            return emotion

    for emotion, needles in _REPLY_HINTS:
        if _contains_any(reply_lower, needles):
            return emotion

    if outcome and outcome in _OUTCOME_MAP:
        return _OUTCOME_MAP[outcome]

    return _DEFAULT


# NOTE: item 1 of the selection order above (AI-supplied emotion field) is
# WRITTEN BUT NOT WIRED — app/brain/schemas.py's ActionItem and the JSON
# the model is asked to return (app/brain/prompt_builder.py) do not carry
# an "emotion" field yet. Adding one touches the action whitelist, the
# JSON validator, and every provider's prompt, which is a larger change
# than this task's blast-radius budget. select_emotion(ai_emotion=...) is
# ready to receive it the moment that field exists; see CHANGES.md.
