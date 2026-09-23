"""
app/brain/json_utils.py

Shared helper for pulling a JSON object out of raw LLM text output.
Used by every AI provider so their error-recovery
logic (stray markdown fences, extra prose despite instructions) stays in
one place instead of being duplicated per provider.
"""

import json
import re


def extract_json_object(raw: str) -> dict:
    """
    Tolerantly pull a JSON object out of `raw`, which may be exactly a JSON
    object, or may have ```json ... ``` fences, or may have stray prose
    around it despite the system prompt asking for JSON only.

    Raises json.JSONDecodeError if nothing parseable is found — callers
    are expected to catch that and treat it as a provider parse failure.
    """
    raw = raw.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace_match:
            raw = brace_match.group(0)

    return json.loads(raw)  # may raise json.JSONDecodeError
