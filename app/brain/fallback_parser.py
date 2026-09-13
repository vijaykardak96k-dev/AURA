"""
app/brain/fallback_parser.py

A simple, dependency-free keyword/regex parser. This is used automatically
whenever no AI provider is available (Gemini unreachable/no key/quota
exceeded, Ollama not running, or AI_PROVIDER=fallback), so AURA never
becomes totally non-functional just because an AI service isn't ready.

It is intentionally NOT treated as an AI/LLM anywhere in the app — see
app/brain/provider.py's FallbackProvider (is_ai = False).

It only covers the most common, unambiguous phrasings, and produces the
SAME raw dict shape a real AI provider would:
    {"actions": [{"action": "OPEN_APP", "parameters": {...}, ...}]}
so schemas.parse_ai_response() can validate it identically either way.

Anything it doesn't recognize returns action UNKNOWN so the caller can
tell the user honestly that it didn't understand, rather than guessing.
"""

import re

from app.brain.identity import get_identity

_APP_ALIASES = {
    "chrome": ["chrome", "google chrome"],
    "edge": ["edge", "microsoft edge"],
    "vscode": ["vs code", "vscode", "visual studio code"],
    "notepad": ["notepad"],
    "calculator": ["calculator", "calc"],
    "explorer": ["file explorer", "explorer", "files"],
    "terminal": ["terminal", "windows terminal", "command prompt", "cmd"],
}

_FOLDER_ALIASES = ["desktop", "documents", "downloads", "pictures", "videos", "music"]


def _match_app(text: str) -> str | None:
    for canonical, aliases in _APP_ALIASES.items():
        for alias in aliases:
            if alias in text:
                return canonical
    return None


def _match_folder(text: str) -> str | None:
    for folder in _FOLDER_ALIASES:
        if folder in text:
            return folder
    return None


def _action(name: str, parameters: dict | None = None, requires_confirmation: bool = False,
            response: str = "") -> dict:
    return {
        "action": name,
        "parameters": parameters or {},
        "requires_confirmation": requires_confirmation,
        "response": response,
    }


def parse_fallback_to_dict(text: str) -> dict:
    """
    Best-effort keyword parse of `text`. Returns a raw dict in the standard
    {"actions": [...]} shape. If nothing matches, returns a single UNKNOWN
    action.

    Includes a SMALL, deliberately limited set of hardcoded identity/
    greeting replies (who are you, who built you, how are you, thanks,
    good morning/night) — this is the explicitly-permitted exception to
    "don't hardcode conversation": general open-ended conversation and
    questions are Gemini/Ollama's job (see prompt_builder.py's CONVERSE
    guidance), not the offline fallback parser's. This handful exists
    purely so identity/greetings still work with zero AI configured.
    """
    t = text.lower().strip()
    identity = get_identity()

    if re.search(r"\bwho\s+(are|r)\s+you\b", t):
        return {"actions": [_action("CONVERSE", {
            "text": f"I'm {identity['name']}, your personal desktop AI assistant. I can understand "
                    f"your voice, answer questions, manage files, launch applications, and help with "
                    f"everyday tasks."
        })]}

    if re.search(r"who\s+(built|created|made)\s+you|who\s+is\s+your\s+(developer|creator)", t):
        return {"actions": [_action("CONVERSE", {
            "text": f"I was built by {identity['owner_name']} as part of the {identity['project_name']}."
        })]}

    if re.search(r"\bhow\s+are\s+you\b", t):
        return {"actions": [_action("CONVERSE", {
            "text": "I'm doing well, thank you. Ready whenever you are."
        })]}

    if re.search(r"\bthank(s|\s+you)\b", t):
        return {"actions": [_action("CONVERSE", {"text": "You're welcome."})]}

    if re.search(r"\bgood\s+(morning|night|evening|afternoon)\b", t):
        return {"actions": [_action("CONVERSE", {"text": "Good to hear from you."})]}


    # --- open / launch / start an app -----------------------------------
    if re.search(r"\b(open|launch|start)\b", t) and "folder" not in t:
        app = _match_app(t)
        if app:
            return {"actions": [_action("OPEN_APP", {"app": app}, response=f"Opening {app}.")]}

    # --- close an app -------------------------------------------------------
    if re.search(r"\bclose\b", t):
        app = _match_app(t)
        if app:
            return {"actions": [_action("CLOSE_APP", {"app": app}, response=f"Closing {app}.")]}

    # --- open a known folder ----------------------------------------------
    if re.search(r"\bopen\b", t) and ("folder" in t or _match_folder(t)):
        folder = _match_folder(t)
        if folder:
            return {"actions": [_action(
                "OPEN_FOLDER", {"path": folder.capitalize()}, response=f"Opening your {folder} folder."
            )]}

    # --- create folder -------------------------------------------------------
    # Match against the ORIGINAL text (case-insensitive) so the captured
    # folder name preserves the user's casing (e.g. "College", not "college").
    m = re.search(r"(?:create|make)\s+(?:a\s+)?folder\s+(?:called|named)?\s*([\w \-]+)", text, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        root = (_match_folder(t) or "desktop").capitalize()
        return {"actions": [_action(
            "CREATE_FOLDER", {"path": f"{root}/{name}"}, response=f"Creating folder '{name}' on your {root}."
        )]}

    # --- take a screenshot -----------------------------------------------------
    if "screenshot" in t:
        return {"actions": [_action("SCREENSHOT", response="Taking a screenshot.")]}

    # --- cpu usage -----------------------------------------------------------------
    if "cpu" in t:
        return {"actions": [_action("CPU_INFO", response="Checking CPU usage.")]}

    # --- ram usage -------------------------------------------------------------------
    if "ram" in t or "memory" in t:
        return {"actions": [_action("RAM_INFO", response="Checking RAM usage.")]}

    # --- battery -----------------------------------------------------------------------
    if "battery" in t:
        return {"actions": [_action("BATTERY_INFO", response="Checking battery status.")]}

    # --- lock --------------------------------------------------------------------------
    if "lock" in t:
        return {"actions": [_action("LOCK_SYSTEM", response="Locking your computer.")]}

    # --- shutdown / restart (dangerous — the security layer will re-confirm anyway) -----
    if "shutdown" in t or "shut down" in t:
        m = re.search(r"(\d+)\s*minute", t)
        if m:
            return {"actions": [_action(
                "SCHEDULE_SHUTDOWN", {"minutes": int(m.group(1))}, requires_confirmation=True,
                response=f"Shutdown in {m.group(1)} minutes requires your confirmation.",
            )]}
        if "cancel" in t:
            return {"actions": [_action("CANCEL_SHUTDOWN", response="Cancelling the scheduled shutdown.")]}
        return {"actions": [_action(
            "SHUTDOWN_SYSTEM", requires_confirmation=True, response="Shutdown requires your confirmation."
        )]}

    if "restart" in t or "reboot" in t:
        return {"actions": [_action(
            "RESTART_SYSTEM", requires_confirmation=True, response="Restart requires your confirmation."
        )]}

    # --- delete (dangerous) ------------------------------------------------------------
    m = re.search(r"delete\s+([\w .\-]+)", text, re.IGNORECASE)
    if m:
        target = m.group(1).strip()
        return {"actions": [_action(
            "DELETE_FILE", {"path": f"Desktop/{target}"}, requires_confirmation=True,
            response=f"Deleting '{target}' requires your confirmation.",
        )]}

    # --- web search ---------------------------------------------------------------------
    m = re.search(r"search (?:the web )?for (.+)", text, re.IGNORECASE)
    if m:
        query = m.group(1).strip()
        return {"actions": [_action("WEB_SEARCH", {"query": query}, response=f"Searching the web for {query}.")]}

    # --- open website -------------------------------------------------------------------
    m = re.search(r"open\s+([\w]+\.[\w]+|\byoutube\b|\bgithub\b|\bgmail\b)", t)
    if m:
        site = m.group(1).strip()
        return {"actions": [_action("OPEN_WEBSITE", {"site": site}, response=f"Opening {site}.")]}

    return {"actions": [_action("UNKNOWN", response="I didn't understand that command. Could you rephrase it?")]}
