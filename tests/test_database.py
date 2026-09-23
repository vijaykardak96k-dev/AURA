"""tests/test_database.py — SQLite persistence, using a temp DB per test."""

import pytest

from app.memory import database


@pytest.fixture()
def db_path(tmp_path):
    p = tmp_path / "test_aura.db"
    database.init_db(p)
    return p


def test_init_db_creates_tables(db_path):
    with database._connect(db_path) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"action_log", "memory", "conversation"}.issubset(tables)


def test_log_and_get_recent_actions(db_path):
    database.log_action("open chrome", "OPEN_APP", True, "Chrome is open.", db_path)
    database.log_action("bad command", "UNKNOWN", False, "I didn't understand.", db_path)

    recent = database.get_recent_actions(limit=5, db_path=db_path)
    assert len(recent) == 2
    assert recent[0]["action"] == "UNKNOWN"  # most recent first
    assert recent[1]["action"] == "OPEN_APP"


def test_remember_recall_forget_roundtrip(db_path):
    database.remember("projects_location", "Documents/Projects", db_path)
    assert database.recall("projects_location", db_path) == "Documents/Projects"

    database.remember("projects_location", "Documents/NewProjects", db_path)
    assert database.recall("projects_location", db_path) == "Documents/NewProjects"

    assert database.forget("projects_location", db_path) is True
    assert database.recall("projects_location", db_path) is None
    assert database.forget("projects_location", db_path) is False  # already gone


def test_recall_missing_key_returns_none(db_path):
    assert database.recall("nonexistent_key", db_path) is None


def test_conversation_trims_to_max_turns(db_path):
    for i in range(database.MAX_CONVERSATION_TURNS + 5):
        database.add_conversation_turn("user", f"message {i}", db_path)

    recent = database.get_recent_conversation(limit=database.MAX_CONVERSATION_TURNS + 5, db_path=db_path)
    assert len(recent) == database.MAX_CONVERSATION_TURNS
    # Oldest kept message should be message 5 (0..4 trimmed away)
    assert recent[0]["text"] == "message 5"
