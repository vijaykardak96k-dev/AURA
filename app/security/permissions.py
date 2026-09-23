"""
app/security/permissions.py

Central security policy. Given a validated ActionItem, decide whether it
can run immediately or must be confirmed.

THE CORE RULE (spec section 10): the AI never determines its own
permission level. Security level always comes from the static whitelist
in app/brain/schemas.py — never from anything the AI/fallback parser said
about itself. Even if a provider's JSON says "requires_confirmation": false
for a DANGEROUS action, this module overrides that and forces confirmation.
There is no code path by which an AI response can bypass this.
"""

from app.brain.schemas import ACTION_WHITELIST, ActionItem, SecurityLevel


def get_security_level(action_name: str) -> SecurityLevel:
    spec = ACTION_WHITELIST.get(action_name)
    return spec.security_level if spec else SecurityLevel.DANGEROUS  # fail closed for unknown names


def needs_confirmation(action: ActionItem) -> bool:
    """
    - DANGEROUS  -> ALWAYS True. The provider's own requires_confirmation
                    hint is ignored entirely; this is not optional.
    - MODERATE   -> True only if the action itself is inherently
                    overwrite-risky (move/rename/write). We require
                    confirmation for all MODERATE actions too, since any of
                    them can silently overwrite existing user data —
                    the provider's hint is again ignored, not trusted.
    - SAFE       -> Never requires confirmation.
    """
    level = get_security_level(action.action)

    if level == SecurityLevel.DANGEROUS:
        return True
    if level == SecurityLevel.MODERATE:
        return True
    return False


def apply_security_policy(action: ActionItem) -> ActionItem:
    """
    Stamp the final, authoritative requires_confirmation value onto the
    action, overwriting whatever the provider suggested. Callers (the
    executor) must use action.requires_confirmation AFTER calling this,
    never the raw value that came back from a provider.
    """
    action.security_level = get_security_level(action.action)
    action.requires_confirmation = needs_confirmation(action)
    return action
