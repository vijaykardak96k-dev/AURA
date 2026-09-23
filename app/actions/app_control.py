"""
Windows application control and discovery.

AURA needs broad-but-safe access to the desktop, so this module is
deliberately layered. Resolution of a spoken/typed app name happens in
this order, stopping at the first hit:

    1. Normalization + alias table
       "OPEN CHRoMe", "task manger", "vs code" all collapse to a single
       canonical key before anything else runs. Matching is never
       case-sensitive and tolerates common speech-to-text spellings.

    2. Windows shell targets
       Task Manager, Control Panel, Settings, Device Manager, ... aren't
       plain .exe paths you can just Popen by name, so each has an
       explicit, fixed launch recipe. These are hardcoded COMMANDS, not
       hardcoded *applications* — the list is small and closed on
       purpose.

    3. Known executable locations for the handful of apps AURA has
       always shipped with (needed anyway for close/restart, which
       require a stable process name).

    4. Dynamic discovery — the Windows "App Paths" registry and Start
       Menu shortcuts. This is what makes "open Spotify", "open Discord",
       "open Steam" work the moment they're installed, with no code
       change. It also powers list_installed_apps() / find_app().

The AI never supplies a command line. It supplies a *name*; everything
about HOW to launch it is decided here, locally. There is no path from a
model's output to subprocess.run(raw_string).
"""

import difflib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app.utils.logger import get_logger

try:
    import winreg
except ImportError:  # not on Windows (e.g. this dev/test sandbox)
    winreg = None

logger = get_logger()

_IS_WINDOWS = sys.platform.startswith("win")


# ------------------------------------------------------------------------
# 1. Name normalization
# ------------------------------------------------------------------------

_NOISE_WORDS = {
    "the", "my", "a", "an", "app", "application", "program",
    "please", "for", "me", "up", "windows",
}

# Common speech-to-text mangles and shorthand. Keys are already
# normalized (lowercase, no punctuation). Values are canonical keys.
_ALIASES = {
    # browsers
    "google chrome": "chrome", "chrom": "chrome", "chrome browser": "chrome",
    "brave browser": "brave", "brave browser browser": "brave",
    "microsoft edge": "edge", "ms edge": "edge",
    "mozilla": "firefox", "mozilla firefox": "firefox", "fire fox": "firefox",

    # editors / dev
    "vs code": "vscode", "visual studio code": "vscode", "vscode": "vscode",
    "code": "vscode", "vs studio code": "vscode", "visual code": "vscode",

    # windows built-ins
    "task manager": "task manager", "task manger": "task manager",
    "taskmanager": "task manager", "taskmgr": "task manager",
    "task master": "task manager", "tax manager": "task manager",
    "control panel": "control panel", "control pannel": "control panel",
    "settings": "settings", "windows settings": "settings",
    "file explorer": "explorer", "files": "explorer",
    "windows explorer": "explorer", "my computer": "explorer",
    "this pc": "explorer", "file manager": "explorer",
    "command prompt": "cmd", "command promt": "cmd", "cmd": "cmd",
    "terminal": "terminal", "windows terminal": "terminal",
    "power shell": "powershell", "powershell": "powershell",
    "device manager": "device manager", "devise manager": "device manager",
    "disk management": "disk management",
    "registry editor": "registry editor", "regedit": "registry editor",
    "event viewer": "event viewer",
    "calculator": "calculator", "calc": "calculator",
    "calculater": "calculator", "calcultor": "calculator",
    "notepad": "notepad", "note pad": "notepad", "notpad": "notepad",
    "paint": "paint", "ms paint": "paint",
    "snipping tool": "snipping tool",
    "sound settings": "sound settings",
    "system information": "system information", "msinfo": "system information",
    "resource monitor": "resource monitor",

    # messaging / media
    "whats app": "whatsapp", "whatsapp": "whatsapp", "what s app": "whatsapp",
    "telegram": "telegram", "spotify": "spotify", "spotifi": "spotify",
    "discord": "discord", "discored": "discord",
}


def _basic_normalize(name: str) -> str:
    """
    Case, punctuation and whitespace only — no filler-word removal.

    Kept separate because dropping filler words too early destroys names
    that legitimately contain them: "what's app" would become "what s"
    once "app" is treated as noise.
    """
    if not name:
        return ""

    text = str(name).lower()
    text = re.sub(r"[^a-z0-9+ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return ""

    # Strip a leading verb if one survived into the parameter, so a
    # provider that sent the whole phrase doesn't make AURA hunt for a
    # program literally called "open spotify".
    return re.sub(r"^(open|launch|start|run|show)\s+", "", text).strip()


def normalize_app_name(name: str) -> str:
    """
    Collapse case, punctuation, whitespace AND filler words, so that
    "OPEN  CHRoMe!", "the chrome app" and "chrome" all become "chrome".
    """
    text = _basic_normalize(name)

    if not text:
        return ""

    words = [w for w in text.split() if w not in _NOISE_WORDS]
    cleaned = " ".join(words).strip()

    return cleaned or text


def resolve_app_key(name: str) -> str:
    """
    Map a spoken/typed name onto a canonical key using the alias table,
    with a conservative fuzzy pass for near-misses ("task manger").

    Returns the normalized name unchanged when nothing matches closely,
    so discovery still gets a chance at apps AURA has never heard of.
    Being wrong here is worse than being unhelpful: silently rewriting an
    unknown name onto a known one would launch something the user never
    asked for.
    """
    base = _basic_normalize(name)

    if not base:
        return ""

    stripped = normalize_app_name(name)

    # Exact matches first, on both forms.
    for candidate in (base, stripped):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
        if candidate in _SHELL_TARGETS or candidate in _KNOWN_LOCATIONS:
            return candidate

    # Fuzzy: only against names AURA definitely knows how to launch, and
    # only at a high cutoff, so a typo resolves but an unrelated app name
    # is left alone.
    known = set(_ALIASES) | set(_SHELL_TARGETS) | set(_KNOWN_LOCATIONS)

    for candidate in (base, stripped):
        close = difflib.get_close_matches(candidate, known, n=1, cutoff=0.82)
        if close:
            resolved = _ALIASES.get(close[0], close[0])
            if resolved != candidate:
                logger.info(f"App name fuzzy-matched: '{name}' -> '{resolved}'")
            return resolved

    return stripped or base


# ------------------------------------------------------------------------
# 2. Windows shell targets
# ------------------------------------------------------------------------
#
# Each entry is (display name, argv). The argv lists are fixed literals —
# nothing from the user or the model is ever interpolated into them.

_SHELL_TARGETS: dict[str, tuple[str, list[str]]] = {
    "task manager": ("Task Manager", ["taskmgr.exe"]),
    "control panel": ("Control Panel", ["control.exe"]),
    "settings": ("Settings", ["cmd", "/c", "start", "", "ms-settings:"]),
    "device manager": ("Device Manager", ["mmc.exe", "devmgmt.msc"]),
    "disk management": ("Disk Management", ["mmc.exe", "diskmgmt.msc"]),
    "event viewer": ("Event Viewer", ["mmc.exe", "eventvwr.msc"]),
    "registry editor": ("Registry Editor", ["regedit.exe"]),
    "resource monitor": ("Resource Monitor", ["resmon.exe"]),
    "system information": ("System Information", ["msinfo32.exe"]),
    "sound settings": ("Sound settings", ["cmd", "/c", "start", "", "ms-settings:sound"]),
    "snipping tool": ("Snipping Tool", ["snippingtool.exe"]),
    "paint": ("Paint", ["mspaint.exe"]),
    "cmd": ("Command Prompt", ["cmd.exe", "/K"]),
    "powershell": ("PowerShell", ["powershell.exe", "-NoExit"]),
    "explorer": ("File Explorer", ["explorer.exe"]),
    "calculator": ("Calculator", ["calc.exe"]),
    "notepad": ("Notepad", ["notepad.exe"]),
}


# ------------------------------------------------------------------------
# 3. Known executable locations
# ------------------------------------------------------------------------

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
    "spotify": [str(Path.home() / "AppData/Roaming/Spotify/Spotify.exe")],
    "notepad": [r"C:\Windows\System32\notepad.exe"],
    "calculator": [r"C:\Windows\System32\calc.exe"],
    "explorer": [r"C:\Windows\explorer.exe"],
    "terminal": [r"C:\Windows\System32\cmd.exe"],
}

_PATH_NAMES = {
    "brave": "brave", "whatsapp": "WhatsApp", "telegram": "Telegram",
    "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
    "vscode": "code", "notepad": "notepad", "calculator": "calc",
    "explorer": "explorer", "terminal": "wt", "spotify": "spotify",
    "discord": "discord",
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
    "spotify": ["Spotify.exe"],
    "discord": ["Discord.exe"],
    "paint": ["mspaint.exe"],
    "explorer": [],
    "terminal": ["WindowsTerminal.exe", "cmd.exe"],
    "task manager": [],
}

_DISPLAY_NAMES = {
    "brave": "Brave Browser",
    "vscode": "VS Code",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
    "explorer": "File Explorer",
    "cmd": "Command Prompt",
    "powershell": "PowerShell",
    "spotify": "Spotify",
    "discord": "Discord",
}


def display_name_for(key: str) -> str:
    if key in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[key]
    if key in _SHELL_TARGETS:
        return _SHELL_TARGETS[key][0]
    return key.title() if key else "that application"


def resolve_app_path(app: str, configured_paths=None):
    """Resolve a canonical app key to a launchable executable path."""
    configured_paths = configured_paths or {}
    key = resolve_app_key(app)

    configured = configured_paths.get(key)
    if configured and configured != "auto" and Path(configured).exists():
        return configured

    for candidate in _KNOWN_LOCATIONS.get(key, []):
        if Path(candidate).exists():
            return candidate

    name = _PATH_NAMES.get(key)
    return shutil.which(name) if name else None


# ------------------------------------------------------------------------
# Launching
# ------------------------------------------------------------------------

def _spawn(argv: list[str]) -> None:
    subprocess.Popen(argv, close_fds=True)


def open_app(app: str, configured_paths=None):
    """
    Open an application by (possibly messy) name.

    Returns (success, message). Never raises.
    """
    if not app or not str(app).strip():
        return False, "Which application would you like me to open?"

    key = resolve_app_key(app)

    if not key:
        return False, "I didn't catch which application you meant."

    # --- Windows shell targets --------------------------------------
    if key in _SHELL_TARGETS:
        label, argv = _SHELL_TARGETS[key]

        if not _IS_WINDOWS:
            return False, f"{label} is only available on Windows."

        try:
            _spawn(list(argv))
            return True, f"{label} is open."
        except OSError as exc:
            logger.error("Shell target launch failed for %s: %s", key, exc)
            return False, f"I couldn't open {label}."

    # --- Known executables ------------------------------------------
    exe = resolve_app_path(key, configured_paths)

    if exe:
        try:
            _spawn([exe])
            return True, f"{display_name_for(key)} is open."
        except OSError as exc:
            logger.error("open_app failed for %s: %s", key, exc)
            return False, f"I couldn't open {display_name_for(key)}."

    # --- Packaged (Store) apps with URI handlers --------------------
    uri_handlers = {"whatsapp": "whatsapp:", "telegram": "tg:", "spotify": "spotify:"}

    if key in uri_handlers and _IS_WINDOWS:
        try:
            _spawn(["cmd", "/c", "start", "", uri_handlers[key]])
            return True, f"{display_name_for(key)} is opening."
        except OSError as exc:
            logger.error("URI launch failed for %s: %s", key, exc)

    # --- Dynamic discovery ------------------------------------------
    return _open_discovered_app(key, spoken_name=str(app).strip())


# ------------------------------------------------------------------------
# 4. App discovery
# ------------------------------------------------------------------------

_APP_PATHS_REGISTRY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

_START_MENU_DIRS = [
    Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs",
]

# Shortcut names that are noise in a user-facing "installed apps" list.
_SHORTCUT_NOISE = re.compile(
    r"uninstall|readme|release notes|documentation|help|website|"
    r"\bmanual\b|license|changelog|report a|feedback",
    re.IGNORECASE,
)


def _best_name_match(name: str, candidates: list[str], cutoff: float = 0.55) -> str | None:
    if not candidates:
        return None
    lookup = {c.lower(): c for c in candidates}
    close = difflib.get_close_matches(name.lower(), list(lookup.keys()), n=1, cutoff=cutoff)
    if close:
        return lookup[close[0]]
    # Fall back to a plain substring match ("vs code" said for "Visual Studio Code.lnk").
    for lowered, original in lookup.items():
        if name.lower() in lowered or lowered in name.lower():
            return original
    return None


def _registry_app_names() -> list[str]:
    """Every exe name registered under HKLM/HKCU App Paths."""
    if winreg is None:
        return []

    names: list[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, _APP_PATHS_REGISTRY_KEY) as key:
                i = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            continue
    return names


def _discover_via_app_paths_registry(name: str) -> str | None:
    """Many Windows installers (Spotify, Discord, Steam, ...) register their
    exe under HKLM/HKCU ...CurrentVersion\\App Paths — this is the same
    mechanism `Win+R` uses to resolve a bare app name."""
    exe_names = _registry_app_names()
    if not exe_names:
        return None

    stems = [n[:-4] if n.lower().endswith(".exe") else n for n in exe_names]
    match = _best_name_match(name, stems)
    if not match:
        return None
    exe_name = match if match.lower().endswith(".exe") else match + ".exe"

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, f"{_APP_PATHS_REGISTRY_KEY}\\{exe_name}") as key:
                path, _ = winreg.QueryValueEx(key, "")
                if path and Path(path).exists():
                    return path
        except OSError:
            continue
    return None


def _start_menu_shortcuts() -> list[Path]:
    shortcuts: list[Path] = []
    for base in _START_MENU_DIRS:
        try:
            if base.exists():
                shortcuts.extend(base.rglob("*.lnk"))
        except OSError:
            continue
    return shortcuts


def _discover_via_start_menu(name: str) -> str | None:
    """Fall back to scanning Start Menu shortcuts (.lnk) by name. AURA
    launches the shortcut directly (Windows resolves it like Explorer
    would) rather than parsing the .lnk's target itself."""
    shortcuts = _start_menu_shortcuts()

    stems = [s.stem for s in shortcuts]
    match = _best_name_match(name, stems)
    if not match:
        return None
    for shortcut in shortcuts:
        if shortcut.stem == match:
            return str(shortcut)
    return None


def discover_app(name: str) -> str | None:
    """Best-effort discovery of an installed app by name, with no hardcoded
    list: checks the App Paths registry first, then Start Menu shortcuts.
    Returns a launchable path, or None if nothing close enough was found.
    """
    name = (name or "").strip()
    if not name:
        return None
    try:
        return _discover_via_app_paths_registry(name) or _discover_via_start_menu(name)
    except Exception as exc:
        logger.error("discover_app failed for %s: %s", name, exc)
        return None


def _open_discovered_app(app: str, spoken_name: str | None = None):
    """
    Last resort: search the machine for an app AURA has no entry for.

    `spoken_name` is what the user actually asked for. It's used for the
    message so AURA echoes their words back ("I couldn't find Obsidian")
    rather than the internal normalized key, which can read oddly once
    filler words have been stripped.
    """
    label = (spoken_name or app or "").strip() or app

    discovered = discover_app(app)

    if not discovered:
        return False, (
            f"I couldn't find {label} installed. "
            "Want me to look through your installed apps?"
        )
    try:
        os.startfile(discovered)  # noqa: S606 — a resolved local path, not a shell string
        return True, f"{display_name_for(app)} is open."
    except (OSError, AttributeError) as exc:
        logger.error("Discovered app launch failed for %s: %s", app, exc)
        return False, f"I found {label}, but couldn't open it."


# ------------------------------------------------------------------------
# Installed application inventory
# ------------------------------------------------------------------------

def list_installed_app_names(limit: int | None = None) -> list[str]:
    """
    Return a de-duplicated, human-readable list of installed applications,
    discovered locally from the Start Menu and the App Paths registry.

    This runs entirely on the machine — the full list is never shipped off
    to an AI provider.
    """
    names: dict[str, str] = {}

    for shortcut in _start_menu_shortcuts():
        stem = shortcut.stem.strip()
        if not stem or _SHORTCUT_NOISE.search(stem):
            continue
        names.setdefault(stem.lower(), stem)

    for exe in _registry_app_names():
        stem = exe[:-4] if exe.lower().endswith(".exe") else exe
        stem = stem.strip()
        if stem and stem.lower() not in names:
            names[stem.lower()] = stem

    ordered = sorted(names.values(), key=str.lower)

    if limit is not None:
        return ordered[:limit]
    return ordered


def list_installed_apps(query: str = "") -> tuple[bool, str]:
    """
    Action handler for "list all installed apps" / "what programs do I
    have". Optionally filtered by `query`.

    The message is kept readable rather than exhaustive: the UI shows the
    full text, and the agent shortens what it *speaks* separately.
    """
    if not _IS_WINDOWS:
        return False, "Listing installed applications only works on Windows."

    try:
        names = list_installed_app_names()
    except Exception as exc:
        logger.error("list_installed_apps failed: %s", exc)
        return False, "I couldn't read the list of installed applications."

    if query:
        needle = normalize_app_name(query)
        names = [n for n in names if needle in n.lower()]

    if not names:
        if query:
            return True, f"I couldn't find anything installed matching '{query}'."
        return True, "I couldn't find any installed applications to list."

    shown = names[:60]
    more = len(names) - len(shown)

    header = (
        f"I found {len(names)} installed application(s)"
        + (f" matching '{query}'" if query else "")
        + ":"
    )

    body = ", ".join(shown)
    tail = f" …and {more} more." if more > 0 else ""

    return True, f"{header} {body}.{tail}"


def find_app(name: str) -> tuple[bool, str]:
    """
    Action handler for "do I have Spotify installed?" / "find Chrome".
    """
    if not name or not str(name).strip():
        return False, "Which application should I look for?"

    if not _IS_WINDOWS:
        return False, "Looking up installed applications only works on Windows."

    key = resolve_app_key(name)
    pretty = display_name_for(key)

    if key in _SHELL_TARGETS:
        return True, f"Yes — {pretty} is built into Windows, so it's always available."

    if resolve_app_path(key):
        return True, f"Yes, {pretty} is installed. Want me to open it?"

    if discover_app(key):
        return True, f"Yes, {pretty} is installed. Want me to open it?"

    try:
        matches = [n for n in list_installed_app_names() if key in n.lower()]
    except Exception:
        matches = []

    if matches:
        return True, (
            f"I didn't find an exact match for '{name}', but these look close: "
            + ", ".join(matches[:8])
            + "."
        )

    return True, f"No — I can't find {pretty} installed on this computer."


# ------------------------------------------------------------------------
# Closing / restarting / running
# ------------------------------------------------------------------------

def close_app(app: str):
    key = resolve_app_key(app)
    names = _PROCESS_NAMES.get(key)

    if names is None:
        # Fall back to a conservative guess for discovered apps: only a
        # single-token name becomes "<name>.exe", never a free-form string.
        if re.fullmatch(r"[a-z0-9]+", key or ""):
            names = [f"{key}.exe"]
        else:
            return False, f"I don't know how to safely close {display_name_for(key)}."

    if not names:
        return False, f"I won't force-close {display_name_for(key)} — that isn't safe."

    if not _IS_WINDOWS:
        return False, "Closing applications is only supported on Windows."

    try:
        closed = False
        for name in names:
            result = subprocess.run(
                ["taskkill", "/IM", name, "/F"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                closed = True
        return (True, f"{display_name_for(key)} is closed.") if closed else (
            False, f"{display_name_for(key)} doesn't seem to be running."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("close_app failed: %s", exc)
        return False, f"I couldn't close {display_name_for(key)}."


def restart_app(app: str):
    ok, message = close_app(app)
    if not ok and "doesn't seem to be running" not in message:
        return False, message
    ok, message = open_app(app)
    if ok:
        return True, f"{display_name_for(resolve_app_key(app))} was restarted."
    return False, message


def list_running_apps():
    if not _IS_WINDOWS:
        return False, "Listing running applications is only supported on Windows."
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        wanted = {
            "chrome.exe": "Chrome", "msedge.exe": "Edge",
            "brave.exe": "Brave", "firefox.exe": "Firefox",
            "Code.exe": "VS Code", "notepad.exe": "Notepad",
            "CalculatorApp.exe": "Calculator", "calc.exe": "Calculator",
            "WindowsTerminal.exe": "Terminal", "cmd.exe": "Terminal",
            "Spotify.exe": "Spotify", "Discord.exe": "Discord",
            "WhatsApp.exe": "WhatsApp", "Telegram.exe": "Telegram",
            "explorer.exe": "File Explorer",
        }
        found = []
        for line in result.stdout.splitlines():
            low = line.lower()
            for proc, label in wanted.items():
                if proc.lower() in low and label not in found:
                    found.append(label)
        if not found:
            return True, "None of the applications I track appear to be running."
        return True, "Currently running: " + ", ".join(found) + "."
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("list_running_apps failed: %s", exc)
        return False, "I couldn't check the running applications."
