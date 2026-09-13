"""
app/brain/schemas.py

The single source of truth for what AURA is allowed to do.

Whichever AI provider produced a response (Gemini, Ollama, or the
deterministic fallback parser), it may ONLY ever describe actions whose
name appears in ACTION_WHITELIST, with parameters matching the declared
schema. Nothing outside this file is ever executed — this is what stands
between "AI suggests something" and "AI runs a command."

Multi-action responses are supported: {"actions": [ {...}, {...} ]}.
Each action is validated INDEPENDENTLY — one unsafe/invalid action in a
batch never lets the others skip validation, and never blocks the valid
ones either; each stands or falls on its own.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SecurityLevel(str, Enum):
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    DANGEROUS = "DANGEROUS"


class ActionName(str, Enum):
    OPEN_APP = "OPEN_APP"
    CLOSE_APP = "CLOSE_APP"
    OPEN_FOLDER = "OPEN_FOLDER"
    CREATE_FOLDER = "CREATE_FOLDER"
    CREATE_FILE = "CREATE_FILE"
    WRITE_FILE = "WRITE_FILE"
    RENAME_FILE = "RENAME_FILE"
    MOVE_FILE = "MOVE_FILE"
    DELETE_FILE = "DELETE_FILE"
    SEARCH_FILES = "SEARCH_FILES"
    SCREENSHOT = "SCREENSHOT"
    CPU_INFO = "CPU_INFO"
    RAM_INFO = "RAM_INFO"
    BATTERY_INFO = "BATTERY_INFO"
    OPEN_WEBSITE = "OPEN_WEBSITE"
    WEB_SEARCH = "WEB_SEARCH"
    LOCK_SYSTEM = "LOCK_SYSTEM"
    RESTART_SYSTEM = "RESTART_SYSTEM"
    SHUTDOWN_SYSTEM = "SHUTDOWN_SYSTEM"
    SCHEDULE_SHUTDOWN = "SCHEDULE_SHUTDOWN"
    CANCEL_SHUTDOWN = "CANCEL_SHUTDOWN"
    REMEMBER = "REMEMBER"
    RECALL = "RECALL"
    FORGET = "FORGET"
    CONVERSE = "CONVERSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    required_params: tuple[str, ...]
    optional_params: tuple[str, ...]
    security_level: SecurityLevel
    description: str


# ---------------------------------------------------------------------------
# THE WHITELIST — the only actions AURA can ever execute.
#
# "path" parameters use the spec-style safe-root-relative format, e.g.
# "Desktop/College" or "Desktop/College/notes.txt" — see
# app/utils/path_safety.py for how these are resolved and validated.
# ---------------------------------------------------------------------------
ACTION_WHITELIST: dict[str, ActionSpec] = {
    ActionName.OPEN_APP: ActionSpec(
        ActionName.OPEN_APP, ("app",), (), SecurityLevel.SAFE,
        "Launch a supported application (chrome, edge, vscode, notepad, calculator, explorer, terminal).",
    ),
    ActionName.CLOSE_APP: ActionSpec(
        ActionName.CLOSE_APP, ("app",), (), SecurityLevel.MODERATE,
        "Terminate a supported, currently-running application.",
    ),
    ActionName.OPEN_FOLDER: ActionSpec(
        ActionName.OPEN_FOLDER, ("path",), (), SecurityLevel.SAFE,
        "Open a folder under a known safe root, e.g. 'Desktop' or 'Documents/Reports'.",
    ),
    ActionName.CREATE_FOLDER: ActionSpec(
        ActionName.CREATE_FOLDER, ("path",), (), SecurityLevel.SAFE,
        "Create a new folder, e.g. path='Desktop/College'.",
    ),
    ActionName.CREATE_FILE: ActionSpec(
        ActionName.CREATE_FILE, ("path",), ("content",), SecurityLevel.SAFE,
        "Create a new text-based file (.txt/.md/.json/.csv/.py), e.g. path='Desktop/College/notes.txt'.",
    ),
    ActionName.WRITE_FILE: ActionSpec(
        ActionName.WRITE_FILE, ("path", "content"), ("append",), SecurityLevel.MODERATE,
        "Write or append text content into an existing file.",
    ),
    ActionName.RENAME_FILE: ActionSpec(
        ActionName.RENAME_FILE, ("path", "new_name"), (), SecurityLevel.MODERATE,
        "Rename a file or folder at 'path' to 'new_name'.",
    ),
    ActionName.MOVE_FILE: ActionSpec(
        ActionName.MOVE_FILE, ("path", "destination"), (), SecurityLevel.MODERATE,
        "Move the file/folder at 'path' into safe-root 'destination'.",
    ),
    ActionName.DELETE_FILE: ActionSpec(
        ActionName.DELETE_FILE, ("path",), (), SecurityLevel.DANGEROUS,
        "Permanently delete a file or folder. Always requires confirmation.",
    ),
    ActionName.SEARCH_FILES: ActionSpec(
        ActionName.SEARCH_FILES, ("query",), ("path",), SecurityLevel.SAFE,
        "Search for files by name inside user-accessible directories.",
    ),
    ActionName.SCREENSHOT: ActionSpec(
        ActionName.SCREENSHOT, (), (), SecurityLevel.SAFE,
        "Capture the screen and save it to the screenshots folder.",
    ),
    ActionName.CPU_INFO: ActionSpec(
        ActionName.CPU_INFO, (), (), SecurityLevel.SAFE, "Report current CPU utilization.",
    ),
    ActionName.RAM_INFO: ActionSpec(
        ActionName.RAM_INFO, (), (), SecurityLevel.SAFE, "Report current RAM usage.",
    ),
    ActionName.BATTERY_INFO: ActionSpec(
        ActionName.BATTERY_INFO, (), (), SecurityLevel.SAFE, "Report battery percentage / charging state.",
    ),
    ActionName.OPEN_WEBSITE: ActionSpec(
        ActionName.OPEN_WEBSITE, ("site",), (), SecurityLevel.SAFE,
        "Open a well-known website in the default browser.",
    ),
    ActionName.WEB_SEARCH: ActionSpec(
        ActionName.WEB_SEARCH, ("query",), (), SecurityLevel.SAFE,
        "Open a search-engine results page for the given query.",
    ),
    ActionName.LOCK_SYSTEM: ActionSpec(
        ActionName.LOCK_SYSTEM, (), (), SecurityLevel.SAFE, "Lock the Windows session immediately.",
    ),
    ActionName.RESTART_SYSTEM: ActionSpec(
        ActionName.RESTART_SYSTEM, (), (), SecurityLevel.DANGEROUS,
        "Restart Windows. Always requires confirmation.",
    ),
    ActionName.SHUTDOWN_SYSTEM: ActionSpec(
        ActionName.SHUTDOWN_SYSTEM, (), (), SecurityLevel.DANGEROUS,
        "Shut down Windows. Always requires confirmation.",
    ),
    ActionName.SCHEDULE_SHUTDOWN: ActionSpec(
        ActionName.SCHEDULE_SHUTDOWN, ("minutes",), (), SecurityLevel.DANGEROUS,
        "Schedule a delayed shutdown. Always requires confirmation.",
    ),
    ActionName.CANCEL_SHUTDOWN: ActionSpec(
        ActionName.CANCEL_SHUTDOWN, (), (), SecurityLevel.SAFE, "Cancel a previously scheduled shutdown.",
    ),
    ActionName.REMEMBER: ActionSpec(
        ActionName.REMEMBER, ("key", "value"), (), SecurityLevel.SAFE,
        "Store a simple personal preference (e.g. a project folder location).",
    ),
    ActionName.RECALL: ActionSpec(
        ActionName.RECALL, ("key",), (), SecurityLevel.SAFE, "Retrieve a previously remembered value.",
    ),
    ActionName.FORGET: ActionSpec(
        ActionName.FORGET, ("key",), (), SecurityLevel.SAFE, "Delete a previously remembered value.",
    ),
    ActionName.CONVERSE: ActionSpec(
        ActionName.CONVERSE, ("text",), (), SecurityLevel.SAFE,
        "A natural conversational reply — greetings, identity questions, general "
        "knowledge questions, small talk. NOT a desktop action; 'text' is spoken/shown as-is.",
    ),
    ActionName.UNKNOWN: ActionSpec(
        ActionName.UNKNOWN, (), (), SecurityLevel.SAFE,
        "Fallback when the request could not be understood as any supported action.",
    ),
}


@dataclass
class ActionItem:
    """One validated (or rejected) action, produced by any provider."""
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False  # raw hint from the AI; the security
                                          # layer (app/security/permissions.py)
                                          # has final say and can override this.
    response: str = ""
    security_level: SecurityLevel = SecurityLevel.SAFE
    valid: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "parameters": self.parameters,
            "requires_confirmation": self.requires_confirmation,
            "response": self.response,
            "security_level": self.security_level.value,
            "valid": self.valid,
            "error": self.error,
        }


class SchemaValidationError(Exception):
    pass


def validate_action(action_name: str, parameters: dict[str, Any]) -> ActionSpec:
    """
    Validate that action_name is whitelisted and all required parameters
    are present. Raises SchemaValidationError otherwise. This is the last
    line of defense before anything from an AI provider touches the
    executor. Unknown/unexpected parameter keys are silently dropped
    rather than rejecting the whole action.
    """
    spec = ACTION_WHITELIST.get(action_name)
    if spec is None:
        raise SchemaValidationError(f"'{action_name}' is not a recognized action.")

    missing = [p for p in spec.required_params if p not in parameters or parameters[p] in (None, "")]
    if missing:
        raise SchemaValidationError(
            f"Action '{action_name}' is missing required parameter(s): {', '.join(missing)}"
        )

    allowed_keys = set(spec.required_params) | set(spec.optional_params)
    for k in [k for k in parameters if k not in allowed_keys]:
        parameters.pop(k, None)

    return spec


def parse_ai_response(raw: dict) -> list[ActionItem]:
    """
    Normalize a raw provider response into a list of ActionItem, each
    independently validated. Accepts either the multi-action shape:
        {"actions": [ {"action": ..., "parameters": {...}}, ... ]}
    or a single-action shape:
        {"action": ..., "parameters": {...}}
    for convenience. Malformed entries become an invalid UNKNOWN ActionItem
    with `.error` set, rather than raising — callers decide what to do with
    invalid items (typically: skip and inform the user), and valid items in
    the same batch are unaffected.
    """
    if "actions" in raw and isinstance(raw["actions"], list):
        raw_items = raw["actions"]
    elif "action" in raw:
        raw_items = [raw]
    else:
        raw_items = []

    if not raw_items:
        return [ActionItem(action=ActionName.UNKNOWN, valid=False, error="No actions found in AI response.")]

    results: list[ActionItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict) or "action" not in entry:
            results.append(ActionItem(action=ActionName.UNKNOWN, valid=False, error="Malformed action entry."))
            continue

        name = str(entry.get("action", "")).strip().upper()
        params = dict(entry.get("parameters", {}) or {})
        hinted_confirm = bool(entry.get("requires_confirmation", False))
        response_text = str(entry.get("response", "") or "")

        try:
            spec = validate_action(name, params)
        except SchemaValidationError as e:
            results.append(ActionItem(
                action=name if name in ACTION_WHITELIST else ActionName.UNKNOWN,
                parameters=params, valid=False, error=str(e),
            ))
            continue

        results.append(ActionItem(
            action=name,
            parameters=params,
            requires_confirmation=hinted_confirm,
            response=response_text,
            security_level=spec.security_level,
            valid=True,
        ))

    return results
