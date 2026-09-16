"""
AURA action whitelist.
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
    RESTART_APP = "RESTART_APP"
    LIST_RUNNING_APPS = "LIST_RUNNING_APPS"

    OPEN_FOLDER = "OPEN_FOLDER"
    LIST_FILES = "LIST_FILES"
    CREATE_FOLDER = "CREATE_FOLDER"
    CREATE_FILE = "CREATE_FILE"
    WRITE_FILE = "WRITE_FILE"
    RENAME_FILE = "RENAME_FILE"
    MOVE_FILE = "MOVE_FILE"
    COPY_FILE = "COPY_FILE"
    DELETE_FILE = "DELETE_FILE"
    SEARCH_FILES = "SEARCH_FILES"

    SCREENSHOT = "SCREENSHOT"
    OPEN_SCREENSHOTS = "OPEN_SCREENSHOTS"

    CPU_INFO = "CPU_INFO"
    RAM_INFO = "RAM_INFO"
    DISK_INFO = "DISK_INFO"
    BATTERY_INFO = "BATTERY_INFO"
    SYSTEM_INFO = "SYSTEM_INFO"

    OPEN_WEBSITE = "OPEN_WEBSITE"
    WEB_SEARCH = "WEB_SEARCH"
    YOUTUBE_SEARCH = "YOUTUBE_SEARCH"
    YOUTUBE_PLAY = "YOUTUBE_PLAY"
    OPEN_BRAVE = "OPEN_BRAVE"

    LOCK_SYSTEM = "LOCK_SYSTEM"
    SLEEP_SYSTEM = "SLEEP_SYSTEM"
    RESTART_SYSTEM = "RESTART_SYSTEM"
    SHUTDOWN_SYSTEM = "SHUTDOWN_SYSTEM"
    SCHEDULE_SHUTDOWN = "SCHEDULE_SHUTDOWN"
    CANCEL_SHUTDOWN = "CANCEL_SHUTDOWN"

    MEDIA_PLAY_PAUSE = "MEDIA_PLAY_PAUSE"
    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    VOLUME_MUTE = "VOLUME_MUTE"

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


ACTION_WHITELIST: dict[str, ActionSpec] = {
    ActionName.OPEN_APP: ActionSpec(ActionName.OPEN_APP, ("app",), (), SecurityLevel.SAFE,
        "Launch a supported application."),
    ActionName.CLOSE_APP: ActionSpec(ActionName.CLOSE_APP, ("app",), (), SecurityLevel.MODERATE,
        "Close a supported running application."),
    ActionName.RESTART_APP: ActionSpec(ActionName.RESTART_APP, ("app",), (), SecurityLevel.MODERATE,
        "Close and reopen a supported application."),
    ActionName.LIST_RUNNING_APPS: ActionSpec(ActionName.LIST_RUNNING_APPS, (), (), SecurityLevel.SAFE,
        "List supported applications currently running."),

    ActionName.OPEN_FOLDER: ActionSpec(ActionName.OPEN_FOLDER, ("path",), (), SecurityLevel.SAFE,
        "Open a safe-root folder."),
    ActionName.LIST_FILES: ActionSpec(ActionName.LIST_FILES, ("path",), ("show_hidden",), SecurityLevel.SAFE,
        "List files and folders inside a safe-root folder."),
    ActionName.CREATE_FOLDER: ActionSpec(ActionName.CREATE_FOLDER, ("path",), (), SecurityLevel.SAFE,
        "Create a folder."),
    ActionName.CREATE_FILE: ActionSpec(ActionName.CREATE_FILE, ("path",), ("content",), SecurityLevel.SAFE,
        "Create a supported text file."),
    ActionName.WRITE_FILE: ActionSpec(ActionName.WRITE_FILE, ("path", "content"), ("append",), SecurityLevel.MODERATE,
        "Write or append text to a file."),
    ActionName.RENAME_FILE: ActionSpec(ActionName.RENAME_FILE, ("path", "new_name"), (), SecurityLevel.MODERATE,
        "Rename a file or folder."),
    ActionName.MOVE_FILE: ActionSpec(ActionName.MOVE_FILE, ("path", "destination"), (), SecurityLevel.MODERATE,
        "Move a file or folder into a safe root."),
    ActionName.COPY_FILE: ActionSpec(ActionName.COPY_FILE, ("path", "destination"), (), SecurityLevel.MODERATE,
        "Copy a file or folder into a safe root."),
    ActionName.DELETE_FILE: ActionSpec(ActionName.DELETE_FILE, ("path",), (), SecurityLevel.DANGEROUS,
        "Delete a file or folder. Always confirm."),
    ActionName.SEARCH_FILES: ActionSpec(ActionName.SEARCH_FILES, ("query",), ("path",), SecurityLevel.SAFE,
        "Search for files by name."),

    ActionName.SCREENSHOT: ActionSpec(ActionName.SCREENSHOT, (), (), SecurityLevel.SAFE,
        "Capture the screen."),
    ActionName.OPEN_SCREENSHOTS: ActionSpec(ActionName.OPEN_SCREENSHOTS, (), (), SecurityLevel.SAFE,
        "Open the screenshots folder."),

    ActionName.CPU_INFO: ActionSpec(ActionName.CPU_INFO, (), (), SecurityLevel.SAFE, "Report CPU usage."),
    ActionName.RAM_INFO: ActionSpec(ActionName.RAM_INFO, (), (), SecurityLevel.SAFE, "Report RAM usage."),
    ActionName.DISK_INFO: ActionSpec(ActionName.DISK_INFO, (), ("drive",), SecurityLevel.SAFE, "Report disk usage."),
    ActionName.BATTERY_INFO: ActionSpec(ActionName.BATTERY_INFO, (), (), SecurityLevel.SAFE, "Report battery status."),
    ActionName.SYSTEM_INFO: ActionSpec(ActionName.SYSTEM_INFO, (), (), SecurityLevel.SAFE, "Report Windows/system information."),

    ActionName.OPEN_WEBSITE: ActionSpec(ActionName.OPEN_WEBSITE, ("site",), (), SecurityLevel.SAFE,
        "Open a website or safe URL."),
    ActionName.WEB_SEARCH: ActionSpec(ActionName.WEB_SEARCH, ("query",), (), SecurityLevel.SAFE,
        "Search the web."),
    ActionName.YOUTUBE_SEARCH: ActionSpec(ActionName.YOUTUBE_SEARCH, ("query",), (), SecurityLevel.SAFE,
        "Search YouTube in Brave."),
    ActionName.YOUTUBE_PLAY: ActionSpec(ActionName.YOUTUBE_PLAY, ("query",), (), SecurityLevel.SAFE,
        "Open a requested YouTube video search in Brave."),
    ActionName.OPEN_BRAVE: ActionSpec(ActionName.OPEN_BRAVE, (), (), SecurityLevel.SAFE,
        "Open Brave Browser."),

    ActionName.LOCK_SYSTEM: ActionSpec(ActionName.LOCK_SYSTEM, (), (), SecurityLevel.SAFE, "Lock Windows."),
    ActionName.SLEEP_SYSTEM: ActionSpec(ActionName.SLEEP_SYSTEM, (), (), SecurityLevel.MODERATE, "Put Windows to sleep."),
    ActionName.RESTART_SYSTEM: ActionSpec(ActionName.RESTART_SYSTEM, (), (), SecurityLevel.DANGEROUS, "Restart Windows. Always confirm."),
    ActionName.SHUTDOWN_SYSTEM: ActionSpec(ActionName.SHUTDOWN_SYSTEM, (), (), SecurityLevel.DANGEROUS, "Shut down Windows. Always confirm."),
    ActionName.SCHEDULE_SHUTDOWN: ActionSpec(ActionName.SCHEDULE_SHUTDOWN, ("minutes",), (), SecurityLevel.DANGEROUS,
        "Schedule a shutdown. Always confirm."),
    ActionName.CANCEL_SHUTDOWN: ActionSpec(ActionName.CANCEL_SHUTDOWN, (), (), SecurityLevel.SAFE, "Cancel scheduled shutdown."),

    ActionName.MEDIA_PLAY_PAUSE: ActionSpec(ActionName.MEDIA_PLAY_PAUSE, (), (), SecurityLevel.SAFE, "Play or pause media."),
    ActionName.VOLUME_UP: ActionSpec(ActionName.VOLUME_UP, (), ("steps",), SecurityLevel.SAFE, "Increase system volume."),
    ActionName.VOLUME_DOWN: ActionSpec(ActionName.VOLUME_DOWN, (), ("steps",), SecurityLevel.SAFE, "Decrease system volume."),
    ActionName.VOLUME_MUTE: ActionSpec(ActionName.VOLUME_MUTE, (), (), SecurityLevel.SAFE, "Mute or unmute system audio."),

    ActionName.REMEMBER: ActionSpec(ActionName.REMEMBER, ("key", "value"), (), SecurityLevel.SAFE, "Remember a value."),
    ActionName.RECALL: ActionSpec(ActionName.RECALL, ("key",), (), SecurityLevel.SAFE, "Recall a remembered value."),
    ActionName.FORGET: ActionSpec(ActionName.FORGET, ("key",), (), SecurityLevel.SAFE, "Forget a remembered value."),
    ActionName.CONVERSE: ActionSpec(ActionName.CONVERSE, ("text",), (), SecurityLevel.SAFE, "Speak a conversational reply."),
    ActionName.UNKNOWN: ActionSpec(ActionName.UNKNOWN, (), (), SecurityLevel.SAFE, "Unsupported request."),
}


@dataclass
class ActionItem:
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
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
    spec = ACTION_WHITELIST.get(action_name)
    if spec is None:
        raise SchemaValidationError(f"'{action_name}' is not a recognized action.")

    missing = [
        p for p in spec.required_params
        if p not in parameters or parameters[p] in (None, "")
    ]
    if missing:
        raise SchemaValidationError(
            f"Action '{action_name}' is missing required parameter(s): {', '.join(missing)}"
        )

    allowed_keys = set(spec.required_params) | set(spec.optional_params)
    for key in list(parameters):
        if key not in allowed_keys:
            parameters.pop(key, None)

    return spec


def parse_ai_response(raw: dict) -> list[ActionItem]:
    if "actions" in raw and isinstance(raw["actions"], list):
        raw_items = raw["actions"]
    elif "action" in raw:
        raw_items = [raw]
    else:
        raw_items = []

    if not raw_items:
        return [ActionItem(action=ActionName.UNKNOWN, valid=False, error="No actions found in AI response.")]

    results = []
    for entry in raw_items:
        if not isinstance(entry, dict) or "action" not in entry:
            results.append(ActionItem(action=ActionName.UNKNOWN, valid=False, error="Malformed action entry."))
            continue

        name = str(entry.get("action", "")).strip().upper()
        params = dict(entry.get("parameters", {}) or {})
        try:
            spec = validate_action(name, params)
        except SchemaValidationError as exc:
            results.append(ActionItem(
                action=name if name in ACTION_WHITELIST else ActionName.UNKNOWN,
                parameters=params, valid=False, error=str(exc)
            ))
            continue

        results.append(ActionItem(
            action=name,
            parameters=params,
            requires_confirmation=bool(entry.get("requires_confirmation", False)),
            response=str(entry.get("response", "") or ""),
            security_level=spec.security_level,
            valid=True,
        ))

    return results
