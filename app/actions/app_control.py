"""
app/actions/app_control.py

Launches and closes a whitelisted set of applications. Never accepts an
arbitrary executable path or command from the LLM — only the fixed keys in
APP_ALIASES (which config/config.yaml can override) are ever launched.

Path resolution order for each app:
  1. explicit path from config.yaml, if set and it exists
  2. common known install locations
  3. shutil.which() (i.e. anywhere on PATH)
If none of those resolve, AURA reports it couldn't find the app instead of
crashing or guessing.
"""

import os
import shutil
import subprocess
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger()

# Canonical app name -> (candidate paths, PATH executable name)
_KNOWN_LOCATIONS: dict[str, list[str]] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "vscode": [
        str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/Code.exe"),
    ],
    "notepad": [r"C:\Windows\System32\notepad.exe"],
    "calculator": [r"C:\Windows\System32\calc.exe"],
    "explorer": [r"C:\Windows\explorer.exe"],
    "terminal": [r"C:\Windows\System32\cmd.exe"],
}

_PATH_NAMES: dict[str, str] = {
    "chrome": "chrome",
    "edge": "msedge",
    "vscode": "code",
    "notepad": "notepad",
    "calculator": "calc",
    "explorer": "explorer",
    "terminal": "wt",
}

# Process-name(s) used for taskkill when closing an app.
_PROCESS_NAMES: dict[str, list[str]] = {
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "vscode": ["Code.exe"],
    "notepad": ["notepad.exe"],
    "calculator": ["CalculatorApp.exe", "calc.exe"],
    "explorer": [],  # never force-close explorer.exe — it's the shell
    "terminal": ["WindowsTerminal.exe", "cmd.exe"],
}

_running_processes: dict[str, subprocess.Popen] = {}


def resolve_app_path(app: str, configured_paths: dict[str, str] | None = None) -> str | None:
    configured_paths = configured_paths or {}

    configured = configured_paths.get(app)
    if configured and configured != "auto" and Path(configured).exists():
        return configured

    for candidate in _KNOWN_LOCATIONS.get(app, []):
        if Path(candidate).exists():
            return candidate

    path_name = _PATH_NAMES.get(app)
    if path_name:
        found = shutil.which(path_name)
        if found:
            return found

    return None


def open_app(app: str, configured_paths: dict[str, str] | None = None) -> tuple[bool, str]:
    app = app.lower().strip()
    if app not in _KNOWN_LOCATIONS:
        return False, f"'{app}' isn't a supported application yet."

    exe = resolve_app_path(app, configured_paths)
    if not exe:
        return False, f"I couldn't find {app} on this computer."

    try:
        proc = subprocess.Popen([exe])
        _running_processes[app] = proc
        logger.info(f"Launched {app} ({exe})")
        return True, f"{app.capitalize()} is open."
    except OSError as e:
        logger.error(f"open_app failed for {app}: {e}")
        return False, f"I couldn't open {app}."


def close_app(app: str) -> tuple[bool, str]:
    app = app.lower().strip()
    process_names = _PROCESS_NAMES.get(app)
    if process_names is None:
        return False, f"'{app}' isn't a supported application yet."
    if not process_names:
        return False, f"I won't force-close {app} for safety reasons."

    try:
        closed_any = False
        for pname in process_names:
            result = subprocess.run(
                ["taskkill", "/IM", pname, "/F"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                closed_any = True
        if closed_any:
            logger.info(f"Closed {app}")
            return True, f"{app.capitalize()} is closed."
        return False, f"{app.capitalize()} doesn't seem to be running."
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"close_app failed for {app}: {e}")
        return False, f"I couldn't close {app}."
