"""
AURA's local identity.

Identity questions are handled locally before any AI provider is called.
This makes AURA's creator/name information deterministic and available
even when Gemini/Groq is unavailable.
"""

import os
import re


def get_identity() -> dict:
    return {
        "name": os.environ.get("AURA_NAME", "AURA").strip() or "AURA",
        "owner_name": os.environ.get("AURA_OWNER_NAME", "Vijay Kardak").strip()
        or "Vijay Kardak",
        "owner_role": os.environ.get("AURA_OWNER_ROLE", "Developer").strip()
        or "Developer",
        "project_name": (
            os.environ.get(
                "AURA_PROJECT_NAME",
                "AURA Personal Desktop Assistant",
            ).strip()
            or "AURA Personal Desktop Assistant"
        ),
    }


def get_local_identity_response(text: str) -> str | None:
    """
    Return a deterministic answer for AURA identity questions.

    This is deliberately narrow. General conversation still goes through
    Gemini/Groq/fallback as before.
    """
    if not text:
        return None

    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    identity = get_identity()

    # Creator / developer / builder questions.
    if re.search(
        r"\bwho\s+(?:are|r)\s+you\b|"
        r"\bwhat\s+(?:is|s)\s+your\s+name\b",
        t,
    ):
        return (
            f"I'm {identity['name']}, your personal desktop assistant. "
            f"I was created and developed by {identity['owner_name']}."
        )

    if re.search(
        r"\bwho\s+(?:built|created|made|developed)\s+you\b|"
        r"\bwho\s+is\s+your\s+(?:developer|creator|owner|maker|builder)\b|"
        r"\bwho\s+developed\s+you\b|"
        r"\bwho\s+made\s+you\b",
        t,
    ):
        return (
            f"I was created and developed by {identity['owner_name']}."
        )

    return None
