"""
Whitelisted Windows application control.
"""

import os
import shutil
import subprocess
from pathlib import Path
from app.utils.logger import get_logger

logger = get_logger()

_KNOWN_LOCATIONS = {
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        str(Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe"),
    ],
    "whatsapp": [
        str(Path.home() / "AppData/Local/WhatsApp/WhatsApp.exe"),
    ],
    "telegram": [
        str(Path.home() / "AppData/Roaming/Telegram Desktop/Telegram.exe"),
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "vscode": [str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/Code.exe")],
    "notepad": [r"C:\Windows\System32\notepad.exe"],
    "calculator": [r"C:\Windows\System32\calc.exe"],
    "explorer": [r"C:\Windows\explorer.exe"],
    "terminal": [r"C:\Windows\System32\cmd.exe"],
}

_PATH_NAMES = {
    "brave": "brave", "whatsapp": "WhatsApp", "telegram": "Telegram",
    "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
    "vscode": "code", "notepad": "notepad", "calculator": "calc",
    "explorer": "explorer", "terminal": "wt",
}

_PROCESS_NAMES = {
    "brave": ["brave.exe"],
    "whatsapp": ["WhatsApp.exe"],
    "telegram": ["Telegram.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "vscode": ["Code.exe"],
    "notepad": ["notepad.exe"],
    "calculator": ["CalculatorApp.exe", "calc.exe"],
    "explorer": [],
    "terminal": ["WindowsTerminal.exe", "cmd.exe"],
}


def resolve_app_path(app: str, configured_paths=None):
    configured_paths = configured_paths or {}
    configured = configured_paths.get(app)
    if configured and configured != "auto" and Path(configured).exists():
        return configured
    for candidate in _KNOWN_LOCATIONS.get(app, []):
        if Path(candidate).exists():
            return candidate
    name = _PATH_NAMES.get(app)
    return shutil.which(name) if name else None


def open_app(app: str, configured_paths=None):
    app = app.lower().strip()
    if app not in _KNOWN_LOCATIONS:
        return False, f"'{app}' isn't a supported application yet."
    exe = resolve_app_path(app, configured_paths)
    try:
        if exe:
            subprocess.Popen([exe])
            display_name = {"brave": "Brave Browser", "vscode": "VS Code", "whatsapp": "WhatsApp", "telegram": "Telegram"}.get(app, app.capitalize())
            return True, f"{display_name} is open."

        # Microsoft Store / packaged installs may not expose a stable .exe.
        # Windows URI handlers are the reliable fallback for these apps.
        if app == "whatsapp":
            subprocess.Popen(["cmd", "/c", "start", "", "whatsapp:"])
            return True, "WhatsApp is opening."
        if app == "telegram":
            subprocess.Popen(["cmd", "/c", "start", "", "tg:"])
            return True, "Telegram is opening."

        return False, f"I couldn't find {app} on this computer."
    except OSError as exc:
        logger.error("open_app failed: %s", exc)
        return False, f"I couldn't open {app}."


def close_app(app: str):
    app = app.lower().strip()
    names = _PROCESS_NAMES.get(app)
    if names is None:
        return False, f"'{app}' isn't a supported application yet."
    if not names:
        return False, f"I won't force-close {app} for safety reasons."
    try:
        closed = False
        for name in names:
            result = subprocess.run(
                ["taskkill", "/IM", name, "/F"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                closed = True
        return (True, f"{app.capitalize()} is closed.") if closed else (
            False, f"{app.capitalize()} doesn't seem to be running."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("close_app failed: %s", exc)
        return False, f"I couldn't close {app}."


def restart_app(app: str):
    ok, message = close_app(app)
    if not ok and "doesn't seem to be running" not in message:
        return False, message
    ok, message = open_app(app)
    if ok:
        return True, f"{app.capitalize()} was restarted."
    return False, message


def list_running_apps():
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        wanted = {
            "chrome.exe": "Chrome", "msedge.exe": "Edge",
            "firefox.exe": "Firefox", "Code.exe": "VS Code",
            "notepad.exe": "Notepad", "CalculatorApp.exe": "Calculator",
            "calc.exe": "Calculator", "WindowsTerminal.exe": "Terminal",
            "cmd.exe": "Terminal",
        }
        found = []
        for line in result.stdout.splitlines():
            low = line.lower()
            for proc, label in wanted.items():
                if proc.lower() in low and label not in found:
                    found.append(label)
        if not found:
            return True, "None of the supported applications appear to be running."
        return True, "Running supported apps: " + ", ".join(found) + "."
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("list_running_apps failed: %s", exc)
        return False, "I couldn't check the running applications."
