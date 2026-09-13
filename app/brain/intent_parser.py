"""
app/brain/intent_parser.py

The single entry point the rest of the app calls: parse_intent(text, ...).

Flow:
  1. Ask the configured AIProvider (Gemini, Ollama, or Fallback — see
     app/brain/provider.py) for a raw action response.
  2. If that provider raises ProviderUnavailable (network error, missing/
     invalid key, quota exceeded, timeout, not-yet-implemented stub, ...),
     fall back to FallbackProvider. AURA never just stops working because
     an AI service is down.
  3. Normalize + validate every action in the response against the
     whitelist schema (schemas.parse_ai_response) — invalid ones become
     UNKNOWN with an explanation, valid ones proceed independently.
  4. Apply the security policy (app/security/permissions.py) to EVERY
     action — this is what forces confirmation on DANGEROUS/MODERATE
     actions regardless of what the provider said.
  5. Resolve simple pronoun references ("it", "that") using the most
     recently created/referenced target, supplied by the caller.
"""

from app.brain.provider import AIProvider, ProviderUnavailable, get_provider
from app.brain.schemas import ActionItem, ActionName, parse_ai_response
from app.security.permissions import apply_security_policy
from app.utils.logger import get_logger

logger = get_logger()

_REFERENCE_WORDS = {"it", "that", "this", "there"}


def _resolve_references(action: ActionItem, last_target: dict | None) -> ActionItem:
    """
    If a parameter value is a bare reference word ('it', 'that') and we
    have a remembered last_target (e.g. {'path': 'Desktop/College'}),
    substitute it in. Implements the simple context/memory behavior from
    spec section 27 (e.g. "Open it" after "Create a folder called College").
    """
    if not last_target:
        return action

    for key, value in list(action.parameters.items()):
        if isinstance(value, str) and value.strip().lower() in _REFERENCE_WORDS:
            if key == "path" and "path" in last_target:
                action.parameters[key] = last_target["path"]
            if key == "destination" and "path" in last_target:
                action.parameters[key] = last_target["path"]

    return action


def parse_intent(
    user_text: str,
    context: str = "",
    last_target: dict | None = None,
    provider: AIProvider | None = None,
) -> list[ActionItem]:
    """
    Main entry point. Returns a list of validated ActionItem (usually one,
    possibly several for a multi-action command). Guaranteed to never
    raise — worst case it returns a single UNKNOWN item with an
    explanatory response, per the "never crash" requirement.

    `provider`, if given, overrides the configured AI_PROVIDER (mainly for
    testing). Otherwise the provider is resolved from the environment.
    """
    if not user_text or not user_text.strip():
        return [ActionItem(action=ActionName.UNKNOWN, response="I didn't catch that. Could you say it again?")]

    active_provider = provider or get_provider()
    raw: dict | None = None
    used_provider_name = active_provider.name

    try:
        raw = active_provider.generate_actions(user_text, context=context)
    except ProviderUnavailable as e:
        logger.info(f"Provider '{active_provider.name}' unavailable ({e}); using fallback parser.")
        from app.brain.provider import FallbackProvider
        fallback = FallbackProvider()
        used_provider_name = fallback.name
        try:
            raw = fallback.generate_actions(user_text, context=context)
        except Exception as fallback_err:  # the fallback parser must never itself fail
            logger.error(f"Fallback parser raised unexpectedly: {fallback_err}")
            raw = None

    if raw is None:
        return [ActionItem(action=ActionName.UNKNOWN, response="I didn't understand that. Could you rephrase it?")]

    actions = parse_ai_response(raw)

    resolved: list[ActionItem] = []
    for action in actions:
        if action.valid:
            action = apply_security_policy(action)
            action = _resolve_references(action, last_target)
        resolved.append(action)

    logger.info(
        f"Parsed intent: '{user_text}' -> "
        f"{[(a.action, a.valid) for a in resolved]} (provider={used_provider_name})"
    )

    return resolved
