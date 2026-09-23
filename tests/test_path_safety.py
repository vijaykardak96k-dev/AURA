"""tests/test_path_safety.py — path traversal and protected-path rejection."""

import pytest

from app.utils.path_safety import PathSecurityError, resolve_safe_path


def test_valid_desktop_path_resolves():
    p = resolve_safe_path("Desktop/College")
    assert p.name == "College"


def test_valid_nested_path_resolves():
    p = resolve_safe_path("Desktop/College/notes.txt")
    assert p.name == "notes.txt"


def test_traversal_dotdot_rejected():
    with pytest.raises(PathSecurityError):
        resolve_safe_path("Desktop/../../etc/passwd")


def test_traversal_dotdot_windows_style_rejected():
    with pytest.raises(PathSecurityError):
        resolve_safe_path("Desktop\\..\\..\\Windows\\System32")


def test_unknown_root_rejected():
    with pytest.raises(PathSecurityError):
        resolve_safe_path("C:/Windows/System32")


def test_bare_windows_root_rejected():
    with pytest.raises(PathSecurityError):
        resolve_safe_path("Windows/System32/evil.exe")


def test_empty_path_rejected():
    with pytest.raises(PathSecurityError):
        resolve_safe_path("")


def test_case_insensitive_root_matches():
    p = resolve_safe_path("desktop/College")
    assert p.name == "College"
