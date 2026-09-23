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


HELP_TEXT = """Here's what I can do.

CONVERSATION
• Just talk to me — questions, small talk, or thinking out loud
• I remember the last few turns, so follow-ups work ("search YouTube" →
  "DevOps tutorials")
• Ask who I am or who built me

VOICE
• Say "AURA" to wake me, then speak your command
• Say "sleep" to send me quiet; "wake up" or Start Listening brings me back
• Say "stop listening" to switch the microphone off entirely
• I don't listen while I'm speaking — type "mute" to cut me off

APPLICATIONS
• Open almost anything installed: "open Chrome", "open Spotify",
  "open Task Manager", "open Settings", "open VS Code"
• Close or restart an app
• "list installed apps" / "do I have Discord installed?"
• "what's running?"

SYSTEM
• CPU, RAM, disk, battery and general system info
• Lock the computer
• Sleep, restart or shut down Windows (I'll confirm first)
• Schedule or cancel a shutdown
• Volume up/down, mute, play/pause

FILES AND FOLDERS
• Open folders and files
• Create folders and text files; write to them
• Copy, move, rename or delete (delete always needs confirmation)
• "find my YAML files", "what did I change today?", "open the latest screenshot"
• I work inside Desktop, Documents, Downloads, Pictures, Videos and Music

WEB
• Open a website, search Google, search or play something on YouTube
• I'll ask which browser if it isn't obvious

SCREENSHOTS
• Take a screenshot; open the screenshots folder

CLIPBOARD
• Read what's on the clipboard

Anything destructive — deleting, shutting down, restarting, overwriting a
file — I'll always check with you first.

No special syntax needed. Just tell me what you want."""


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
