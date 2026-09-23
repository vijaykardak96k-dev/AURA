"""
tests/test_app_control.py

Application name resolution: the layer that turns whatever the user said
(or whatever Whisper thought they said) into something AURA can actually
launch.

The requirement is that matching is never case-sensitive and tolerates
small transcription mistakes — "open task manger" must reach Task
Manager — while staying conservative enough that an unrelated name isn't
silently rewritten into something the user didn't ask for.

These run on any OS: nothing here launches a process.
"""

import pytest

from app.actions import app_control
from app.actions.app_control import (
    normalize_app_name,
    resolve_app_key,
)


# ------------------------------------------------------------ normalization

@pytest.mark.parametrize("spoken", [
    "chrome", "Chrome", "CHROME", "CHRoMe", "  chrome  ",
    "chrome!", "the chrome app", "google chrome",
])
def test_every_casing_and_wrapping_of_chrome_resolves_the_same(spoken):
    assert resolve_app_key(spoken) == "chrome"


def test_normalization_strips_filler_and_punctuation():
    assert normalize_app_name("  the   Notepad app!! ") == "notepad"


def test_normalization_strips_a_leaked_verb():
    """If a provider puts the whole phrase in the 'app' parameter rather
    than just the name, don't try to launch a program called
    'open spotify'."""
    assert normalize_app_name("open spotify") == "spotify"


def test_normalization_of_empty_input_is_empty_not_an_error():
    assert normalize_app_name("") == ""
    assert normalize_app_name(None) == ""


# ------------------------------------------------------------ typo tolerance

@pytest.mark.parametrize("heard,expected", [
    ("task manger", "task manager"),
    ("task manager", "task manager"),
    ("taskmgr", "task manager"),
    ("TASK MANGER", "task manager"),
    ("calculater", "calculator"),
    ("notpad", "notepad"),
    ("control pannel", "control panel"),
    ("vs code", "vscode"),
    ("visual studio code", "vscode"),
    ("file explorer", "explorer"),
    ("command promt", "cmd"),
    ("what's app", "whatsapp"),
])
def test_common_speech_to_text_mistakes_resolve_correctly(heard, expected):
    assert resolve_app_key(heard) == expected


def test_unknown_app_names_are_passed_through_untouched():
    """
    An app AURA has never heard of must reach discovery unchanged rather
    than being fuzzy-matched onto something unrelated. "Obsidian" is not
    "explorer".
    """
    assert resolve_app_key("obsidian") == "obsidian"
    assert resolve_app_key("blender") == "blender"


def test_fuzzy_matching_is_conservative_enough_not_to_invent_matches():
    """A genuinely different word must not be dragged onto a known key
    just because it shares a few letters."""
    assert resolve_app_key("manager") != "task manager"


# --------------------------------------------------------------- launching

def test_open_app_with_no_name_asks_rather_than_guessing():
    ok, message = app_control.open_app("")
    assert ok is False
    assert "?" in message


def test_open_app_never_raises_for_an_unknown_application(monkeypatch):
    """A missing app is a conversation, not an exception."""
    monkeypatch.setattr(app_control, "discover_app", lambda name: None)

    ok, message = app_control.open_app("some application that does not exist")

    assert ok is False
    assert "couldn't find" in message.lower()


def test_missing_app_message_offers_a_next_step():
    """Spec: 'Application not found.' is the wrong tone — offer to help."""
    ok, message = app_control.open_app("definitely-not-installed-xyz")
    assert ok is False
    assert "installed apps" in message.lower()


def test_close_app_refuses_to_force_close_explorer():
    """Killing explorer.exe takes the whole desktop shell down with it."""
    ok, message = app_control.close_app("file explorer")
    assert ok is False
    assert "safe" in message.lower()


def test_close_app_refuses_a_free_form_name_it_cannot_map():
    """close_app must never build a taskkill target out of an arbitrary
    multi-word string."""
    ok, _ = app_control.close_app("some long unknown thing")
    assert ok is False


# ----------------------------------------------------------- app inventory

def test_list_installed_apps_filters_by_query(monkeypatch):
    monkeypatch.setattr(app_control, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        app_control,
        "list_installed_app_names",
        lambda limit=None: ["Spotify", "Steam", "Notepad++", "Discord"],
    )

    ok, message = app_control.list_installed_apps("spotify")

    assert ok is True
    assert "Spotify" in message
    assert "Discord" not in message


def test_list_installed_apps_summarizes_rather_than_dumping_everything(monkeypatch):
    """Spec: don't read out hundreds of application names."""
    monkeypatch.setattr(app_control, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        app_control,
        "list_installed_app_names",
        lambda limit=None: [f"App {i}" for i in range(200)],
    )

    ok, message = app_control.list_installed_apps()

    assert ok is True
    assert "200" in message
    assert "more" in message.lower()


def test_find_app_reports_a_windows_builtin_as_always_available(monkeypatch):
    monkeypatch.setattr(app_control, "_IS_WINDOWS", True)

    ok, message = app_control.find_app("task manager")

    assert ok is True
    assert "yes" in message.lower()


def test_find_app_says_no_honestly_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(app_control, "_IS_WINDOWS", True)
    monkeypatch.setattr(app_control, "resolve_app_path", lambda *a, **k: None)
    monkeypatch.setattr(app_control, "discover_app", lambda name: None)
    monkeypatch.setattr(app_control, "list_installed_app_names", lambda limit=None: [])

    ok, message = app_control.find_app("someapp")

    assert ok is True
    assert "no" in message.lower()


def test_find_app_with_no_name_asks_instead_of_guessing():
    ok, message = app_control.find_app("")
    assert ok is False
    assert "?" in message
