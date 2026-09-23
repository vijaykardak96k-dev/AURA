"""
app/brain/intent_parser.py

The single entry point the rest of the app calls: parse_intent(text, ...).
"""

from app.brain.identity import get_local_identity_response
from app.brain.provider import AIProvider, ProviderUnavailable, get_provider
from app.brain.schemas import ActionItem, ActionName, parse_ai_response
from app.security.permissions import apply_security_policy
from app.utils.logger import get_logger

logger = get_logger()

_REFERENCE_WORDS = {"it", "that", "this", "there", "that screenshot",
                    "this screenshot", "that folder", "this folder",
                    "that file", "this file"}


def _resolve_references(action: ActionItem, last_target: dict | None) -> ActionItem:
    """Resolve simple references such as 'it' using the last target."""
    if not last_target:
        return action

    for key, value in list(action.parameters.items()):
        if isinstance(value, str) and value.strip().lower() in _REFERENCE_WORDS:
            if key == "path" and "path" in last_target:
                action.parameters[key] = last_target["path"]
            if key == "destination" and "path" in last_target:
                action.parameters[key] = last_target["path"]

    return action


def _local_context_action(user_text: str) -> ActionItem | None:
    """
    Deterministic handling for AURA's most useful conversational references.
    These commands bypass the LLM so "that screenshot" always means the
    screenshot AURA most recently created.
    """
    import re

    text = (user_text or "").strip().lower()
    if not text:
        return None

    if re.search(r"\b(open|show|launch)\b.*\b(that|this)\b.*\bscreenshot\b", text):
        return ActionItem(
            action=ActionName.OPEN_FILE,
            parameters={"path": "", "context": "last_screenshot"},
            response="",
        )

    if re.search(r"\b(open|show|launch)\b.*\b(that|this)\b.*\bfolder\b", text):
        return ActionItem(
            action=ActionName.OPEN_FOLDER,
            parameters={"path": "", "context": "last_folder"},
            response="",
        )

    if re.search(r"\b(open|show|launch)\b.*\b(that|this)\b.*\b(file|document)\b", text):
        return ActionItem(
            action=ActionName.OPEN_FILE,
            parameters={"path": "", "context": "last_file"},
            response="",
        )

    if text in {
        "open screenshots",
        "open screenshot folder",
        "open the screenshot folder",
        "show screenshots",
    }:
        return ActionItem(
            action=ActionName.OPEN_SCREENSHOTS,
            parameters={},
        )

    return None


def parse_intent(
    user_text: str,
    context: str = "",
    last_target: dict | None = None,
    provider: AIProvider | None = None,
) -> list[ActionItem]:
    """
    Main entry point.

    Identity questions are answered locally BEFORE Groq/fallback.
    All other requests follow the existing AI/security pipeline.
    """
    if not user_text or not user_text.strip():
        return [
            ActionItem(
                action=ActionName.UNKNOWN,
                response="I didn't catch that. Could you say it again?",
            )
        ]

    # ---------------------------------------------------------------
    # AURA CORE IDENTITY
    # ---------------------------------------------------------------
    # This must happen before the AI provider. Otherwise an LLM can
    # invent or incorrectly describe AURA's creator.
    local_identity = get_local_identity_response(user_text)
    if local_identity:
        logger.info("Handled identity question locally.")
        return [
            ActionItem(
                action=ActionName.CONVERSE,
                parameters={"text": local_identity},
                response=local_identity,
            )
        ]

    local_context = _local_context_action(user_text)
    if local_context:
        logger.info("Handled context command locally.")
        return [local_context]

    active_provider = provider or get_provider()
    raw: dict | None = None
    used_provider_name = active_provider.name

    # Skip the network entirely when the provider already knows it can't
    # work (no API key, SDK missing). Otherwise every offline command
    # would pay a full connect timeout before falling back.
    if active_provider.is_ai:
        try:
            provider_usable = active_provider.is_available()
        except Exception:
            provider_usable = False
    else:
        provider_usable = True

    try:
        if not provider_usable:
            raise ProviderUnavailable(
                f"{active_provider.name} is not configured or reachable."
            )

        raw = active_provider.generate_actions(
            user_text,
            context=context,
        )

    except ProviderUnavailable as e:
        logger.info(
            f"Provider '{active_provider.name}' unavailable ({e}); "
            "using fallback parser."
        )

        from app.brain.provider import FallbackProvider

        fallback = FallbackProvider()
        used_provider_name = fallback.name

        try:
            raw = fallback.generate_actions(
                user_text,
                context=context,
            )
        except Exception as fallback_err:
            logger.error(
                f"Fallback parser raised unexpectedly: {fallback_err}"
            )
            raw = None

    if raw is None:
        return [
            ActionItem(
                action=ActionName.UNKNOWN,
                response="I didn't understand that. Could you rephrase it?",
            )
        ]

    actions = parse_ai_response(raw)

    resolved: list[ActionItem] = []

    for action in actions:
        if action.valid:
            action = apply_security_policy(action)
            action = _resolve_references(action, last_target)

        resolved.append(action)

    logger.info(
        f"Parsed intent: '{user_text}' -> "
        f"{[(a.action, a.valid) for a in resolved]} "
        f"(provider={used_provider_name})"
    )

    return resolved
