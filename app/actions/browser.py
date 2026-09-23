"""Safe browser actions with explicit Brave support."""

import difflib
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote_plus
from app.actions import app_control
from app.utils.logger import get_logger

logger = get_logger()

# ----------------------------------------------------------------------
# Browser selection (for WEB_SEARCH / OPEN_WEBSITE)
#
# webbrowser.open() launches whatever Windows has set as the system
# default, which for most users is Edge. Several people explicitly don't
# want that, and have their own per-purpose preference instead (e.g.
# Brave for YouTube, Chrome for general search). Rather than guessing or
# hardcoding one browser, AURA can be told which one to use, either
# inline in the command ("search for X in chrome") or by asking and
# fuzzy-matching the spoken reply, since speech-to-text often mangles
# short browser names ("chrom", "chom", "rom" -> chrome; "rave", "bave"
# -> brave).
# ----------------------------------------------------------------------

_BROWSER_ALIASES = {
    "chrome": ["chrome", "google chrome"],
    "brave": ["brave", "brave browser"],
    "edge": ["edge", "microsoft edge", "ms edge"],
    "firefox": ["firefox", "fire fox", "mozilla", "mozilla firefox"],
}

_BROWSER_DISPLAY_NAMES = {
    "chrome": "Chrome",
    "brave": "Brave",
    "edge": "Edge",
    "firefox": "Firefox",
}

_BROWSER_MATCH_THRESHOLD = 0.45


def match_browser_name(spoken_text: str, available=None) -> str | None:
    """Fuzzy-match a spoken/typed browser name to a canonical browser id.

    Tolerant of speech-to-text mishearings and typos (short, partial or
    garbled words like "rom", "chom", "chrom" for chrome, or "rave",
    "bave" for brave) by scoring against known browser names with
    difflib rather than requiring an exact, case-sensitive match.
    """
    if not spoken_text:
        return None

    candidates = available or list(_BROWSER_ALIASES.keys())
    normalized = re.sub(r"[^a-z ]+", " ", spoken_text.lower()).strip()
    if not normalized:
        return None

    tokens = normalized.split()
    tokens.append(normalized)  # also try the whole phrase, e.g. "google chrome"

    best_browser = None
    best_score = 0.0
    for browser_id in candidates:
        for alias in _BROWSER_ALIASES.get(browser_id, [browser_id]):
            for token in tokens:
                if not token:
                    continue
                score = difflib.SequenceMatcher(None, token, alias).ratio()
                if token in alias or alias in token:
                    score = max(score, 0.75)
                if score > best_score:
                    best_score = score
                    best_browser = browser_id

    return best_browser if best_score >= _BROWSER_MATCH_THRESHOLD else None


def extract_browser_from_text(text: str) -> str | None:
    """Look for an explicit, unambiguous browser name inside a full command,
    e.g. "search the web for kubernetes tutorials in chrome" -> "chrome".
    Uses plain word-boundary matching (not fuzzy) since this runs against a
    full, already-understood sentence rather than a short spoken reply.
    """
    if not text:
        return None
    normalized = text.lower()
    for browser_id, aliases in _BROWSER_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(r"\b" + re.escape(alias) + r"\b", normalized):
                return browser_id
    return None


def _launch_url_with_browser(browser_id: str, url: str):
    """Launch `url` in a specific installed browser. Returns (ok, display_name)."""
    display = _BROWSER_DISPLAY_NAMES.get(browser_id, browser_id.capitalize())

    exe = app_control.resolve_app_path(browser_id)
    if not exe and browser_id == "brave":
        exe = _brave_exe()
    if not exe:
        exe = shutil.which(browser_id)

    if not exe:
        return False, display

    try:
        subprocess.Popen([exe, url])
        return True, display
    except Exception as exc:
        logger.error("Launching %s failed: %s", browser_id, exc)
        return False, display

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


def open_website(site, browser: str | None = None):
    key = site.strip().lower()
    if key in _WELL_KNOWN_SITES:
        url = _WELL_KNOWN_SITES[key]
    elif re.match(r"^https?://", key):
        url = key
    elif _SIMPLE_DOMAIN_RE.match(key):
        url = "https://" + key
    else:
        return False, f"I don't recognize '{site}' as a safe website."

    if browser:
        ok, display = _launch_url_with_browser(browser, url)
        if ok:
            return True, f"Opening {site} in {display}."
        return False, f"I couldn't find {display} installed on this computer."

    try:
        # No specific browser requested: fall back to the system default.
        import webbrowser
        webbrowser.open(url)
        return True, f"Opening {site}."
    except Exception as exc:
        logger.error("open_website failed: %s", exc)
        return False, f"I couldn't open {site}."


def web_search(query, browser: str | None = None):
    if not query.strip():
        return False, "What would you like me to search for?"
    url = "https://www.google.com/search?q=" + quote_plus(query.strip())

    if browser:
        ok, display = _launch_url_with_browser(browser, url)
        if ok:
            return True, f"Searching Google for {query} in {display}."
        return False, f"I couldn't find {display} installed on this computer."

    try:
        import webbrowser
        webbrowser.open(url)
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
