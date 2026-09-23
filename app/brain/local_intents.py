"""
app/brain/local_intents.py

A small, table-driven "LOCAL COMMANDS" layer, separate from both the AI
providers (Groq) and the offline app/brain/fallback_parser.py.

Why a third place for pattern matching, instead of just growing
fallback_parser.py further? The two exist for different reasons and must
stay independent:

  - fallback_parser.py only runs when NO AI provider is available at all
    (spec: AURA must never go fully silent just because Groq is
    unreachable). It is exercised by tests/test_fallback_parser.py.

  - This module runs on EVERY command, AI or not, purely for latency:
    a high-confidence match here means AURA skips the network round trip
    entirely (STT -> AI -> parse -> action -> AI -> TTS collapses to
    STT -> action -> TTS). See app/core/agent.py's _handle_local_control(),
    which checks this table before ever building a provider request.

A few phrasings are deliberately NOT matched here even though they look
like good candidates (e.g. "open chrome", "what's my cpu usage") because
existing behavior/tests pin them to always reach the AI provider. Add new
rules below rather than widening those.

Each rule is (compiled_pattern, action_name, param_builder). param_builder
receives the regex Match and returns the parameters dict for that action.
"""

import re
from typing import Callable, Optional

Rule = tuple["re.Pattern[str]", str, Callable[["re.Match[str]"], dict]]

_RULES: list[Rule] = []


def _rule(pattern: str, action: str, params: Callable[["re.Match[str]"], dict] = lambda m: {}) -> None:
    _RULES.append((re.compile(pattern), action, params))


# ------------------------------------------------------------------------
# Simple, zero-parameter system actions.
# (CPU/RAM/BATTERY/DISK/SYSTEM_INFO are intentionally NOT included here —
# they're pinned by tests/test_agent.py to reach the AI provider.)
# ------------------------------------------------------------------------

_rule(r"^(?:take|capture)\s+a?\s*screenshot$", "SCREENSHOT")
_rule(r"^screenshot$", "SCREENSHOT")
_rule(r"^lock(?:\s+(?:the\s+)?(?:pc|computer|windows|screen))?$", "LOCK_SYSTEM")
_rule(r"^(?:list\s+)?running\s+apps?$", "LIST_RUNNING_APPS")
_rule(r"^what(?:'s|s)?\s+running$", "LIST_RUNNING_APPS")

_rule(r"^(?:mute|unmute)(?:\s+(?:the\s+)?(?:volume|sound|audio))?$", "VOLUME_MUTE")
_rule(r"^volume\s+up$", "VOLUME_UP")
_rule(r"^(?:turn\s+up\s+the\s+volume|increase\s+(?:the\s+)?volume)$", "VOLUME_UP")
_rule(r"^volume\s+down$", "VOLUME_DOWN")
_rule(r"^(?:turn\s+down\s+the\s+volume|decrease\s+(?:the\s+)?volume|lower\s+(?:the\s+)?volume)$", "VOLUME_DOWN")
_rule(r"^(?:play|pause)(?:\s+(?:the\s+)?(?:music|media|song))?$", "MEDIA_PLAY_PAUSE")

# ------------------------------------------------------------------------
# Open a supported app locally (skips the AI round trip for common apps).
#
# "chrome" is intentionally excluded — tests/test_agent.py pins
# "open chrome" to reach the AI provider, and changing that would be a
# behavior regression, not an improvement.
# ------------------------------------------------------------------------

_LOCAL_APP_NAMES = (
    "calculator", "calc", "notepad", "explorer", "file explorer",
    "terminal", "command prompt", "cmd", "vscode", "vs code",
    "visual studio code", "edge", "firefox",
)

_APP_CANONICAL = {
    "calc": "calculator", "calculator": "calculator",
    "notepad": "notepad",
    "explorer": "explorer", "file explorer": "explorer",
    "terminal": "terminal", "command prompt": "terminal", "cmd": "terminal",
    "vscode": "vscode", "vs code": "vscode", "visual studio code": "vscode",
    "edge": "edge",
    "firefox": "firefox",
}

_APP_NAMES_PATTERN = "|".join(sorted((re.escape(n) for n in _LOCAL_APP_NAMES), key=len, reverse=True))

_rule(
    rf"^(?:open|launch|start)\s+({_APP_NAMES_PATTERN})$",
    "OPEN_APP",
    lambda m: {"app": _APP_CANONICAL[m.group(1)]},
)


# ------------------------------------------------------------------------
# File intelligence (spec item 5): natural-language file lookups that
# don't need an AI round trip to understand. These MUST be checked before
# the generic "open <anything>" catch-all further below, since phrases
# like "open the latest screenshot" would otherwise be misread as an app
# name called "the latest screenshot".
# ------------------------------------------------------------------------

_rule(
    r"^open\s+(?:the\s+)?latest\s+screenshot$",
    "OPEN_LATEST_FILE",
    lambda m: {"path": "screenshots"},
)

_rule(
    r"^open\s+(?:the\s+)?(?:latest|newest|most\s+recent)\s+(?:file\s+)?(?:in\s+(?:my\s+)?)?(desktop|documents|downloads|pictures|videos|music)$",
    "OPEN_LATEST_FILE",
    lambda m: {"path": m.group(1)},
)

_rule(
    r"^(?:find|search for)\s+(?:my\s+)?(?:(.+?)\s+)?(yaml|yml|json|python|csv|pdf|image|picture|photo|video|word|excel|spreadsheet|markdown|text|zip|archive)\s+files?$",
    "FIND_FILES_BY_TYPE",
    lambda m: {"keyword": (m.group(1) or "").strip(), "extension": m.group(2)},
)

_rule(
    r"^(?:show me|what are|list)\s+(?:the\s+)?files?\s+(?:i\s+)?(?:modified|changed|edited)\s+today$",
    "LIST_RECENT_FILES",
    lambda m: {"hours": 24},
)

_rule(
    r"^what\s+(?:files\s+)?did\s+i\s+(?:modify|change|edit)\s+today$",
    "LIST_RECENT_FILES",
    lambda m: {"hours": 24},
)

_rule(
    r"^create\s+(?:a\s+)?folder\s+for\s+(?:my\s+)?(.+?)(?:\s+project)?$",
    "CREATE_FOLDER",
    lambda m: {"path": f"Desktop/{m.group(1).strip().title()}"},
)


# ------------------------------------------------------------------------
# Activity history (spec item 9): "what did you do today?"
# ------------------------------------------------------------------------

_rule(r"^what\s+did\s+you\s+do\s+today$", "ACTIVITY_SUMMARY", lambda m: {"period": "today"})
_rule(r"^what\s+did\s+you\s+do\s+yesterday$", "ACTIVITY_SUMMARY", lambda m: {"period": "yesterday"})
_rule(r"^(?:show|what's|whats)\s+(?:my\s+|the\s+)?(?:recent\s+)?activity(?:\s+today)?$", "ACTIVITY_SUMMARY", lambda m: {"period": "today"})


# Anything else after "open/launch/start <name>" that isn't one of the
# above falls through to OPEN_APP too, letting app_control.discover_app()
# try the Windows App Paths registry / Start Menu before giving up — this
# is what lets "open Spotify"/"open Discord"/"open Steam" work without a
# hardcoded entry, as long as it's actually installed.
#
# "chrome"/"brave"/"whatsapp"/"telegram"/"youtube" are excluded here: they
# already have their own specific handling elsewhere (agent.py's existing
# fast path, or intentionally left to reach the AI provider per
# tests/test_agent.py) and must not be re-routed by this generic catch-all.
#
# Reference words ("it", "that", "this folder", ...) are also excluded —
# "open it" needs app/brain/intent_parser.py's last-target resolution
# (see memory_manager.set_last_target), which only runs on the AI path.
# Matching those here would silently break that coreference feature.
_rule(
    r"^(?:open|launch|start)\s+"
    r"(?!chrome\b|brave\b|whatsapp\b|telegram\b|youtube\b"
    r"|the\s+latest\b|latest\b|newest\b|most\s+recent\b"
    r"|it$|that$|this$|there$"
    r"|that\s+screenshot$|this\s+screenshot$"
    r"|that\s+folder$|this\s+folder$|that\s+file$|this\s+file$)"
    r"([a-z0-9][a-z0-9 ]{1,30})$",
    "OPEN_APP",
    lambda m: {"app": m.group(1).strip()},
)


def match_local_intent(normalized_text: str) -> Optional[tuple[str, dict]]:
    """
    Try every rule against already-normalized text (lowercased,
    punctuation stripped — see AuraAgent._normalize_command).

    Returns (action_name, parameters) for the first match, or None if
    nothing matched confidently enough — callers should fall through to
    the AI provider / fallback parser in that case, never guess further.
    """
    for pattern, action, build_params in _RULES:
        match = pattern.match(normalized_text)
        if match:
            return action, build_params(match)
    return None
