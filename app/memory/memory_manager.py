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


def build_context_string(limit: int = 6) -> str:
    """
    A short, plain-text summary of recent turns, suitable for passing to an
    AI provider as light context. Kept deliberately small — this is not a
    full chat history, just enough for basic follow-up references.
    """
    try:
        turns = database.get_recent_conversation(limit)
    except Exception as e:
        logger.error(f"build_context_string failed: {e}")
        return ""

    lines = [f"{t['role']}: {t['text']}" for t in turns]
    return "\n".join(lines)
