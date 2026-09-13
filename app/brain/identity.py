"""
app/brain/identity.py

AURA's configurable identity — who it is, who built it, what project it's
part of. Read from environment variables so nothing personal is
hard-coded through the source tree; set these in .env.

Used in two places:
  - app/brain/prompt_builder.py embeds this into the system prompt so
    Gemini/Ollama answer identity questions consistently.
  - app/brain/fallback_parser.py uses it directly for the small, explicitly
    permitted set of hardcoded identity/greeting replies when no AI
    provider is available.
"""

import os


def get_identity() -> dict:
    return {
        "name": os.environ.get("AURA_NAME", "AURA").strip() or "AURA",
        "owner_name": os.environ.get("AURA_OWNER_NAME", "").strip() or "its developer",
        "owner_role": os.environ.get("AURA_OWNER_ROLE", "Developer").strip() or "Developer",
        "project_name": (
            os.environ.get("AURA_PROJECT_NAME", "").strip() or "AURA Personal Desktop Assistant"
        ),
    }
