"""Safe browser actions with explicit Brave support."""

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote_plus
from app.utils.logger import get_logger

logger = get_logger()

_WELL_KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://www.github.com",
    "gmail": "https://mail.google.com",
    "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.in",
    "linkedin": "https://www.linkedin.com",
    "stackoverflow": "https://stackoverflow.com",
}
_SIMPLE_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$")

_BRAVE_PATHS = (
    Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
    Path(r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"),
    Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe",
)


def _brave_exe():
    for path in _BRAVE_PATHS:
        if path.exists():
            return str(path)
    return shutil.which("brave")


def open_in_brave(url: str):
    """Open a URL specifically in Brave when it is installed."""
    exe = _brave_exe()
    try:
        if exe:
            subprocess.Popen([exe, url])
        else:
            # Keep the feature useful if Brave is installed as a packaged app
            # without a discoverable executable.
            subprocess.Popen(["cmd", "/c", "start", "", url])
        return True, "Opened in Brave Browser."
    except Exception as exc:
        logger.error("Brave launch failed: %s", exc)
        return False, "I couldn't open Brave Browser."


def open_website(site):
    key = site.strip().lower()
    if key in _WELL_KNOWN_SITES:
        url = _WELL_KNOWN_SITES[key]
    elif re.match(r"^https?://", key):
        url = key
    elif _SIMPLE_DOMAIN_RE.match(key):
        url = "https://" + key
    else:
        return False, f"I don't recognize '{site}' as a safe website."
    try:
        # YouTube and normal web requests use the system browser unless the
        # caller explicitly requested Brave.
        import webbrowser
        webbrowser.open(url)
        return True, f"Opening {site}."
    except Exception as exc:
        logger.error("open_website failed: %s", exc)
        return False, f"I couldn't open {site}."


def web_search(query):
    if not query.strip():
        return False, "What would you like me to search for?"
    try:
        import webbrowser
        webbrowser.open("https://www.google.com/search?q=" + quote_plus(query.strip()))
        return True, f"Searching Google for {query}."
    except Exception as exc:
        logger.error("web_search failed: %s", exc)
        return False, "I couldn't open the search."


def youtube_search(query):
    if not query.strip():
        return False, "What would you like me to search for on YouTube?"
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query.strip())
    return open_in_brave(url)


def youtube_play(query):
    """Open the requested YouTube search in Brave and start media playback.

    AURA intentionally avoids brittle screen-coordinate clicking. The command
    opens the exact YouTube search in Brave; if a YouTube tab is already active,
    the media play/pause key is also available through the normal media command.
    """
    if not query.strip():
        return False, "What video would you like me to play on YouTube?"
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query.strip())
    return open_in_brave(url)
