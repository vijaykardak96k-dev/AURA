"""
app/security/confirmation.py

Builds the human-readable confirmation prompt for an action, and parses
the user's spoken/typed reply into yes/no. Used for both voice
confirmation (app/core/agent.py's WAITING_FOR_CONFIRMATION state) and any
GUI Yes/No buttons — one parser, one set of accepted phrases, for both.

IMPORTANT: silence/timeout is never treated as confirmation — an
unanswered confirmation always defaults to cancellation. See
app/core/agent.py's confirmation timeout handling.
"""

import re

from app.brain.schemas import ActionItem


def build_confirmation_prompt(action: ActionItem) -> str:
    name = action.action
    params = action.parameters

    if name == "DELETE_FILE":
        return f"Are you sure you want to permanently delete '{params.get('path', '?')}'? This cannot be undone."
    if name == "SHUTDOWN_SYSTEM":
        return "Are you sure you want me to shut down the computer?"
    if name == "RESTART_SYSTEM":
        return "Are you sure you want me to restart the computer?"
    if name == "SCHEDULE_SHUTDOWN":
        mins = params.get("minutes", "?")
        return f"Are you sure you want to schedule a shutdown in {mins} minute(s)?"
    if name == "MOVE_FILE":
        return f"Move '{params.get('path', '?')}' to '{params.get('destination', '?')}'? This may overwrite an existing file."
    if name == "RENAME_FILE":
        return f"Rename '{params.get('path', '?')}' to '{params.get('new_name', '?')}'?"
    if name == "WRITE_FILE":
        return f"Write to '{params.get('path', '?')}'? This will modify the file's contents."
    if name == "CLOSE_APP":
        return f"Close {params.get('app', 'this application')}?"

    return f"Confirm: {action.response or name}?"


# Exact-phrase matches (after normalization — lowercased, punctuation
# stripped, including apostrophes: "don't" -> "dont").
AFFIRMATIVE_PHRASES = {
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "confirm", "correct",
    "affirmative", "do it", "go ahead", "please do", "yes please",
}
NEGATIVE_PHRASES = {
    "no", "nope", "nah", "negative", "dont", "cancel", "stop", "not now",
    "dont do it", "cancel that", "never mind", "nevermind", "no thanks",
}

# First-word matches — catches natural replies with extra words, e.g.
# "yeah sure go ahead" or "no I changed my mind", without needing every
# possible full phrase enumerated above.
_AFFIRMATIVE_FIRST_WORDS = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok", "confirm", "affirmative", "correct"}
_NEGATIVE_FIRST_WORDS = {"no", "nope", "nah", "negative", "dont", "cancel", "stop"}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()


def _classify(text: str) -> str:
    """Returns 'yes', 'no', or 'unclear'. Internal — is_affirmative/is_negative
    are the public interface, kept as separate functions for readability at
    call sites, but both delegate here so a phrase can never be classified
    as both (e.g. "don't do it" contains "do it" as a raw substring, but
    must resolve to "no", not both)."""
    n = _normalize(text)
    if not n:
        return "unclear"

    neg_exact = n in NEGATIVE_PHRASES
    pos_exact = n in AFFIRMATIVE_PHRASES
    if neg_exact and not pos_exact:
        return "no"
    if pos_exact and not neg_exact:
        return "yes"

    first_word = n.split()[0]
    if first_word in _NEGATIVE_FIRST_WORDS:
        return "no"
    if first_word in _AFFIRMATIVE_FIRST_WORDS:
        return "yes"

    # Word-boundary phrase search (not raw substring) as a last resort —
    # negative checked first, since a decline embedded anywhere in a
    # longer reply should win over an incidentally-matching positive
    # fragment.
    for phrase in NEGATIVE_PHRASES:
        if " " in phrase and re.search(rf"\b{re.escape(phrase)}\b", n):
            return "no"
    for phrase in AFFIRMATIVE_PHRASES:
        if " " in phrase and re.search(rf"\b{re.escape(phrase)}\b", n):
            return "yes"

    return "unclear"


def is_affirmative(text: str) -> bool:
    return _classify(text) == "yes"


def is_negative(text: str) -> bool:
    return _classify(text) == "no"
