"""
app/actions/browser.py

Opens websites via the system default browser. To avoid ever navigating to
an arbitrary/dangerous URL constructed from raw AI output, we:
  - only resolve 'site' against a small whitelist of well-known names, or
  - require it to already look like a plausible bare domain (simple regex),
  - and for search queries, we build the URL ourselves with urlencode —
    the model only ever supplies the query text, never a URL.
"""

import re
import webbrowser
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

_SIMPLE_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}$")


def open_website(site: str) -> tuple[bool, str]:
    key = site.strip().lower()

    if key in _WELL_KNOWN_SITES:
        url = _WELL_KNOWN_SITES[key]
    elif _SIMPLE_DOMAIN_RE.match(key):
        url = f"https://{key}"
    else:
        return False, f"I don't recognize '{site}' as a website I can safely open."

    try:
        webbrowser.open(url)
        logger.info(f"Opened website: {url}")
        return True, f"Opening {key}."
    except Exception as e:
        logger.error(f"open_website failed: {e}")
        return False, f"I couldn't open {site}."


def web_search(query: str) -> tuple[bool, str]:
    query = query.strip()
    if not query:
        return False, "What would you like me to search for?"
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    try:
        webbrowser.open(url)
        logger.info(f"Web search opened for: {query}")
        return True, f"Here's what I found for {query}."
    except Exception as e:
        logger.error(f"web_search failed: {e}")
        return False, "I couldn't perform that search."
