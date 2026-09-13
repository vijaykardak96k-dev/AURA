"""
app/memory/database.py

SQLite persistence — three tables:
  1. action_log    — history of every command AURA executed (for the UI's
                      "Recent Actions" panel and for debugging).
  2. memory        — simple key/value personal preferences ("remember that
                      my projects are in Documents/Projects").
  3. conversation   — a short rolling log of user/assistant turns, used to
                      build the "context" string passed to AI providers and
                      to resolve references like "it" across turns.

Uses only the standard library sqlite3 module — no extra dependency.
Do not store unnecessary personal information (spec section 13) — only
what's needed for action history, preferences, and short-term context.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.utils.paths import DATA_DIR, ensure_project_dirs

DB_PATH = DATA_DIR / "aura.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    command_text TEXT,
    action TEXT NOT NULL,
    success INTEGER NOT NULL,
    result_message TEXT
);

CREATE TABLE IF NOT EXISTS memory (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    role TEXT NOT NULL,       -- 'user' or 'assistant'
    text TEXT NOT NULL
);
"""

MAX_CONVERSATION_TURNS = 20


@contextmanager
def _connect(db_path: Path = DB_PATH):
    ensure_project_dirs()
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


# --- action log ---------------------------------------------------------

def log_action(command_text: str, action: str, success: bool, result_message: str,
               db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO action_log (timestamp, command_text, action, success, result_message) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), command_text, action, int(success), result_message),
        )


def get_recent_actions(limit: int = 10, db_path: Path = DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- memory (key/value preferences) --------------------------------------

def remember(key: str, value: str, db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key.strip().lower(), value, datetime.now().isoformat(timespec="seconds")),
        )


def recall(key: str, db_path: Path = DB_PATH) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM memory WHERE key = ?", (key.strip().lower(),)
        ).fetchone()
        return row[0] if row else None


def forget(key: str, db_path: Path = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM memory WHERE key = ?", (key.strip().lower(),))
        return cur.rowcount > 0


# --- conversation (short rolling context) --------------------------------

def add_conversation_turn(role: str, text: str, db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO conversation (timestamp, role, text) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), role, text),
        )
        # Trim to the most recent MAX_CONVERSATION_TURNS rows.
        conn.execute(
            "DELETE FROM conversation WHERE id NOT IN "
            "(SELECT id FROM conversation ORDER BY id DESC LIMIT ?)",
            (MAX_CONVERSATION_TURNS,),
        )


def get_recent_conversation(limit: int = 6, db_path: Path = DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM conversation ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
