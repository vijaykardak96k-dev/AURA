"""
AURA local identity and built-in help.
"""

import os
import re


def get_identity() -> dict:
    return {
        "name": os.environ.get("AURA_NAME", "AURA").strip() or "AURA",
        "owner_name": os.environ.get("AURA_OWNER_NAME", "Vijay Kardak").strip() or "Vijay Kardak",
        "owner_role": os.environ.get("AURA_OWNER_ROLE", "Developer").strip() or "Developer",
        "project_name": os.environ.get(
            "AURA_PROJECT_NAME", "AURA Personal Desktop Assistant"
        ).strip() or "AURA Personal Desktop Assistant",
    }


HELP_TEXT = """Here is what I can do.

App Control:
• Open applications — Brave, Chrome, Edge, Firefox, VS Code, Notepad, Calculator, Explorer, Terminal, WhatsApp, Telegram
• Close applications
• Restart applications
• List running applications

Browser:
• Open Chrome, Edge, or Firefox
• Open a website or URL
• Search Google
• Search YouTube in Brave
• Open and search YouTube in Brave
• Play/watch a requested YouTube search in Brave

Files and Folders:
• Create folders and supported text files
• Write to files
• Delete files or folders
• Rename files or folders
• Move files or folders
• Copy files or folders
• Open folders
• List files in a folder
• Find files by name

Windows Control:
• Lock the computer
• Sleep the computer
• Restart Windows
• Shut down Windows
• Schedule a shutdown
• Cancel a scheduled shutdown

System:
• Check CPU usage
• Check RAM usage
• Check disk usage
• Check battery
• Get system information

Screenshots:
• Take a screenshot
• Open the screenshots folder

Media:
• Play or pause media
• Increase or decrease volume
• Mute or unmute
• Stop AURA while she is speaking by saying "stop" or "mute"

AURA:
• Remember something
• Recall something
• Forget something
• Show recent activity through the interface
• Tell you about AURA and its developer
• Say "sleep" or "go to sleep" to put AURA into sleep mode; say "AURA" to wake her

Dangerous actions such as deleting files, closing apps, restarting, shutting down, or scheduled shutdown always require confirmation.

Just tell me what you want in normal language."""


def get_local_identity_response(text: str) -> str | None:
    if not text:
        return None

    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    identity = get_identity()

    if (
        re.search(r"\b(aura\s+)?help\b", t)
        or t in {"what can you do", "what all can you do", "show me your commands"}
    ):
        return HELP_TEXT

    if re.search(r"\bwho\s+(?:are|r)\s+you\b|\bwhat\s+(?:is|s)\s+your\s+name\b", t):
        return (
            f"I'm {identity['name']}, your personal desktop assistant. "
            f"I was created and developed by {identity['owner_name']}."
        )

    if re.search(
        r"\bwho\s+(?:built|created|made|developed)\s+you\b|"
        r"\bwho\s+is\s+your\s+(?:developer|creator|owner|maker|builder)\b|"
        r"\bwho\s+developed\s+you\b|\bwho\s+made\s+you\b",
        t,
    ):
        return f"I was created and developed by {identity['owner_name']}."

    return None
