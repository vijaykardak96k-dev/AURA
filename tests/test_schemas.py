"""tests/test_schemas.py — schema validation, whitelist enforcement, multi-action parsing."""

import pytest

from app.brain.schemas import (
    ActionName,
    SchemaValidationError,
    SecurityLevel,
    parse_ai_response,
    validate_action,
)


def test_valid_action_passes():
    spec = validate_action("OPEN_APP", {"app": "chrome"})
    assert spec.security_level == SecurityLevel.SAFE


def test_unknown_action_rejected():
    with pytest.raises(SchemaValidationError):
        validate_action("DELETE_SYSTEM32", {})


def test_missing_required_param_rejected():
    with pytest.raises(SchemaValidationError):
        validate_action("OPEN_APP", {})


def test_extra_unexpected_param_is_stripped_not_rejected():
    params = {"app": "chrome", "shell_command": "rm -rf /"}
    validate_action("OPEN_APP", params)
    assert "shell_command" not in params


def test_dangerous_action_classified_correctly():
    spec = validate_action("DELETE_FILE", {"path": "Desktop/notes.txt"})
    assert spec.security_level == SecurityLevel.DANGEROUS


def test_moderate_action_classified_correctly():
    spec = validate_action("MOVE_FILE", {"path": "Desktop/notes.txt", "destination": "Documents"})
    assert spec.security_level == SecurityLevel.MODERATE


def test_parse_ai_response_single_action_shape():
    raw = {"action": "OPEN_APP", "parameters": {"app": "chrome"}}
    items = parse_ai_response(raw)
    assert len(items) == 1
    assert items[0].valid
    assert items[0].action == "OPEN_APP"


def test_parse_ai_response_multi_action_shape():
    raw = {
        "actions": [
            {"action": "CREATE_FOLDER", "parameters": {"path": "Desktop/College"}},
            {"action": "CREATE_FILE", "parameters": {"path": "Desktop/College/project.txt"}},
        ]
    }
    items = parse_ai_response(raw)
    assert len(items) == 2
    assert all(i.valid for i in items)
    assert items[0].action == "CREATE_FOLDER"
    assert items[1].action == "CREATE_FILE"


def test_parse_ai_response_one_bad_action_does_not_block_others():
    raw = {
        "actions": [
            {"action": "OPEN_APP", "parameters": {"app": "chrome"}},
            {"action": "NOT_A_REAL_ACTION", "parameters": {}},
        ]
    }
    items = parse_ai_response(raw)
    assert len(items) == 2
    assert items[0].valid is True
    assert items[1].valid is False


def test_parse_ai_response_rejects_shell_command_style_action():
    """The AI must never be able to smuggle a shell command through as an action name."""
    raw = {"action": "powershell", "parameters": {"command": "Remove-Item -Recurse C:\\"}}
    items = parse_ai_response(raw)
    assert items[0].valid is False


def test_parse_ai_response_empty_input_returns_unknown():
    items = parse_ai_response({})
    assert len(items) == 1
    assert items[0].action == ActionName.UNKNOWN
    assert items[0].valid is False
