"""
app/utils/paths.py

Central place for resolving filesystem locations.
Never hard-codes a username or drive letter — always derives paths from
Path.home() so the project works on any Windows account.
"""

from pathlib import Path
import sys

# Base of the whole AURA project (…/AURA), regardless of where it's launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"

# Known Windows user folders. On non-Windows dev machines these still resolve
# to sensible (if nonexistent) paths under the home directory, which keeps
# the module importable for testing on any OS.
HOME = Path.home()

KNOWN_FOLDERS = {
    "desktop": HOME / "Desktop",
    "documents": HOME / "Documents",
    "downloads": HOME / "Downloads",
    "pictures": HOME / "Pictures",
    "videos": HOME / "Videos",
    "music": HOME / "Music",
}


def ensure_project_dirs() -> None:
    """Create data/logs/screenshots dirs if they don't exist yet."""
    for d in (DATA_DIR, LOGS_DIR, DEFAULT_SCREENSHOT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resolve_known_folder(name: str) -> Path | None:
    """
    Resolve a spoken location name ('desktop', 'downloads', ...) to a real
    Path. Returns None if the name isn't recognized — caller must handle
    that gracefully rather than crashing.
    """
    if not name:
        return None
    key = name.strip().lower()
    return KNOWN_FOLDERS.get(key)


def is_windows() -> bool:
    return sys.platform.startswith("win")
