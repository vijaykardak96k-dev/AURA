"""
app/brain/persona.py

AURA's voice, for the paths that never reach the AI provider.

Most conversational personality comes from the system prompt in
prompt_builder.py. But a lot of what the user actually hears is generated
locally — fast-path app launches, offline-mode replies, error messages —
and if those read like log lines then AURA sounds like a command runner no
matter how good the prompt is.

This module is deliberately tiny and dependency-free: a few rotating
phrase pools plus two helpers. Rotation (rather than random choice) means
the same phrase never lands twice in a row, which is the single biggest
thing that makes a canned response feel canned.
"""

import random
import re
from itertools import cycle

# --------------------------------------------------------------------------
# Phrase pools
# --------------------------------------------------------------------------

_POOLS: dict[str, list[str]] = {
    # Before/while doing something the user asked for.
    "ack": [
        "On it.",
        "Sure.",
        "Right away.",
        "Consider it handled.",
        "One moment.",
        "Got it.",
        "Doing that now.",
    ],
    # After something finished cleanly.
    "done": [
        "Done.",
        "All set.",
        "That's done.",
        "Handled.",
        "There you go.",
    ],
    # Something went wrong, but nothing dramatic.
    "trouble": [
        "That didn't work, I'm afraid.",
        "I couldn't get that one to go through.",
        "That one didn't take.",
        "No luck with that.",
    ],
    # Going to sleep.
    "sleep": [
        "Alright. I'll stay quiet until you wake me.",
        "Okay — going quiet. Say AURA when you need me.",
        "Understood. I'll be here when you call.",
    ],
    # Waking up.
    "wake": [
        "I'm here.",
        "Awake. What do you need?",
        "Right here.",
        "Listening.",
    ],
    # Bare wake word with no command after it.
    "attention": [
        "Yes?",
        "I'm listening.",
        "Go ahead.",
        "Yes — what do you need?",
    ],
    # Didn't understand.
    "unclear": [
        "I didn't quite catch that. Could you say it again?",
        "Sorry — say that once more?",
        "That one got past me. Try again?",
    ],
}

_CYCLES: dict[str, cycle] = {}


def _next(pool: str) -> str:
    """Return the next phrase from a pool, shuffled once per full pass."""
    if pool not in _CYCLES:
        items = list(_POOLS.get(pool, []))
        if not items:
            return ""
        random.shuffle(items)
        _CYCLES[pool] = cycle(items)
    return next(_CYCLES[pool])


def acknowledge(kind: str = "ack") -> str:
    """A short, varied acknowledgement. Never the same one twice running."""
    return _next(kind)


def warm_up_failure_message(message: str) -> str:
    """
    Take a blunt failure string and make it sound like a person said it,
    without hiding what actually happened.

    Only rewrites the handful of flat phrasings AURA produces internally —
    anything already conversational is left alone.
    """
    if not message:
        return acknowledge("trouble")

    text = str(message).strip()
    lowered = text.lower()

    if lowered.startswith("i couldn't find") and "install" in lowered:
        return text  # app_control already phrases this one well

    if lowered in {"application not found.", "app not found."}:
        return (
            "I couldn't find that application. "
            "Want me to search your installed apps?"
        )

    if lowered.startswith("i don't understand"):
        return acknowledge("unclear")

    return text


# --------------------------------------------------------------------------
# Speech shortening
# --------------------------------------------------------------------------

def speakable_summary(text: str, max_items: int = 8, max_chars: int = 320) -> str:
    """
    Shorten a long, list-like result into something reasonable to say out
    loud. The UI still receives the full text — this only affects TTS.

    "I found 214 installed application(s): A, B, C, …"
        -> "I found 214 installed applications. The first few are A, B, C.
            The full list is on screen."
    """
    if not text:
        return ""

    text = str(text).strip()

    if len(text) <= max_chars:
        return text

    head, sep, rest = text.partition(":")

    if sep and "," in rest:
        items = [item.strip(" .") for item in rest.split(",") if item.strip(" .")]
        if len(items) > max_items:
            preview = ", ".join(items[:max_items])
            return (
                f"{head.strip().rstrip(':')}. "
                f"The first few are {preview}. "
                "The full list is on screen."
            )

    # Not a list — cut at a sentence boundary instead of mid-word.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for sentence in sentences:
        if len(out) + len(sentence) > max_chars:
            break
        out += sentence + " "

    out = out.strip()

    if not out:
        out = text[:max_chars].rsplit(" ", 1)[0]

    return out + " The rest is on screen."
