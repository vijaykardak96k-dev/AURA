"""
app/memory/memory_manager.py

Sits between the rest of the app and app/memory/database.py. Responsibilities:

  - Wraps remember/recall/forget/log_action with friendly (success, message)
    return values, matching the shape every other action module uses.
  - Tracks the in-process "last target" (the most recently created folder/
    file) so app/brain/intent_parser.py can resolve "open it" / "create a
    file inside it" without a full NLP coreference system — just a small,
    explicit piece of state, per spec section 27 ("keep this simple").
  - Builds a short natural-language context string from recent conversation
    turns to pass to AI providers.

This module intentionally does NOT talk to file_manager, app_control, etc.
— it only owns memory/context concerns. app/actions/executor.py is what
calls into this module for REMEMBER/RECALL/FORGET actions and for logging.
"""

from app.memory import database
from app.utils.logger import get_logger
from datetime import datetime, timedelta

logger = get_logger()

# Simple in-process state for "it"/"that" resolution within a session.
# Deliberately NOT persisted to SQLite — it's short-term working memory,
# distinct from the durable key/value `memory` table.
_last_target: dict | None = None


def init() -> None:
    database.init_db()


def set_last_target(target: dict) -> None:
    global _last_target
    _last_target = target
    logger.info(f"Last target updated: {target}")


def get_last_target() -> dict | None:
    return _last_target


def remember(key: str, value: str) -> tuple[bool, str]:
    try:
        database.remember(key, value)
        return True, f"Got it — I'll remember that {key} is {value}."
    except Exception as e:
        logger.error(f"remember failed: {e}")
        return False, "I couldn't save that."


def recall_as_message(key: str) -> tuple[bool, str]:
    try:
        value = database.recall(key)
    except Exception as e:
        logger.error(f"recall failed: {e}")
        return False, "I couldn't check my memory right now."

    if value is None:
        return False, f"I don't have anything remembered for '{key}'."
    return True, f"{key} is {value}."


def forget(key: str) -> tuple[bool, str]:
    try:
        removed = database.forget(key)
    except Exception as e:
        logger.error(f"forget failed: {e}")
        return False, "I couldn't update my memory."

    if not removed:
        return False, f"I didn't have anything remembered for '{key}'."
    return True, f"Okay, I've forgotten '{key}'."


def get_all_remembered() -> list[dict]:
    try:
        return database.get_all_memory()
    except Exception as e:
        logger.error(f"get_all_remembered failed: {e}")
        return []


def log_action(command_text: str, action: str, success: bool, result_message: str) -> None:
    try:
        database.log_action(command_text, action, success, result_message)
    except Exception as e:
        logger.error(f"log_action failed: {e}")


def get_recent_actions(limit: int = 10) -> list[dict]:
    try:
        return database.get_recent_actions(limit)
    except Exception as e:
        logger.error(f"get_recent_actions failed: {e}")
        return []


def add_conversation_turn(role: str, text: str) -> None:
    try:
        database.add_conversation_turn(role, text)
    except Exception as e:
        logger.error(f"add_conversation_turn failed: {e}")


def build_context_string(limit: int = 10) -> str:
    """
    A short, plain-text summary of recent turns plus anything the user has
    asked AURA to remember, suitable for passing to an AI provider as light
    context. Kept deliberately small — this is not a full chat history, just
    enough for basic follow-up references ("what do you know about him?",
    "what project are we working on?", "what did I ask you to do earlier?").
    """
    sections = []

    try:
        facts = database.get_all_memory()
    except Exception as e:
        logger.error(f"build_context_string (memory) failed: {e}")
        facts = []

    if facts:
        fact_lines = [f"- {f['key']}: {f['value']}" for f in facts]
        sections.append("Remembered facts:\n" + "\n".join(fact_lines))

    if _last_target:
        sections.append(f"Most recently created/targeted item: {_last_target}")

    try:
        turns = database.get_recent_conversation(limit)
    except Exception as e:
        logger.error(f"build_context_string (conversation) failed: {e}")
        turns = []

    if turns:
        turn_lines = [f"{t['role']}: {t['text']}" for t in turns]
        sections.append("Recent conversation:\n" + "\n".join(turn_lines))

    return "\n\n".join(sections)


def summarize_period(period: str = "today") -> tuple[bool, str]:
    """Build a natural-language summary of what AURA did today/yesterday,
    from the persistent action_log (spec item 9 — "what did you do today?").
    """
    now = datetime.now()
    if period == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "yesterday"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
        label = "today"

    try:
        rows = database.get_actions_between(
            start.isoformat(timespec="seconds"),
            end.isoformat(timespec="seconds") if end else None,
        )
    except Exception as e:
        logger.error(f"summarize_period failed: {e}")
        return False, "I couldn't check my activity history."

    successful = [r for r in rows if r["success"]]

    if not successful:
        return True, f"I haven't done anything yet {label}." if label == "today" else f"I didn't do anything {label}."

    # Collapse consecutive duplicate actions ("Opened Brave" x3 -> once)
    # into a readable, deduplicated list, in the order they happened.
    seen = []
    for row in successful:
        entry = row["result_message"] or row["action"]
        if entry not in seen:
            seen.append(entry)

    preview = seen[:10]
    more = f", and {len(seen) - 10} more thing(s)" if len(seen) > 10 else ""
    return True, f"Here's what I did {label}: " + "; ".join(preview) + more + "."
