"""tests/test_security.py — security level enforcement and AI-override protection."""

from app.brain.schemas import ActionItem, SecurityLevel
from app.security.permissions import apply_security_policy, get_security_level, needs_confirmation


def test_dangerous_action_always_needs_confirmation():
    action = ActionItem(action="DELETE_FILE", parameters={"path": "Desktop/x.txt"})
    assert needs_confirmation(action) is True


def test_safe_action_never_needs_confirmation():
    action = ActionItem(action="OPEN_APP", parameters={"app": "chrome"})
    assert needs_confirmation(action) is False


def test_ai_cannot_bypass_confirmation_for_dangerous_action():
    """
    This is the critical security test: even if the AI's own JSON claims
    requires_confirmation=False for a DANGEROUS action, the security layer
    must override it to True.
    """
    action = ActionItem(
        action="SHUTDOWN_SYSTEM",
        parameters={},
        requires_confirmation=False,  # AI said "no confirmation needed" — must be ignored
    )
    result = apply_security_policy(action)
    assert result.requires_confirmation is True


def test_ai_cannot_downgrade_delete_to_safe():
    action = ActionItem(action="DELETE_FILE", parameters={"path": "Desktop/x.txt"})
    result = apply_security_policy(action)
    assert result.security_level == SecurityLevel.DANGEROUS
    assert result.requires_confirmation is True


def test_moderate_action_requires_confirmation_regardless_of_ai_hint():
    action = ActionItem(action="MOVE_FILE", parameters={"path": "a", "destination": "b"},
                         requires_confirmation=False)
    result = apply_security_policy(action)
    assert result.requires_confirmation is True


def test_unknown_action_name_fails_closed_as_dangerous():
    assert get_security_level("SOME_MADE_UP_ACTION") == SecurityLevel.DANGEROUS
