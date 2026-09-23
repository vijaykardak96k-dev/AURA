"""
app/utils/path_safety.py

Every filesystem action in AURA goes through resolve_safe_path() before
touching disk. This is the filesystem-safety layer required by the spec:
no ../ traversal, no absolute drive paths (C:\\Windows, C:\\System32, etc.),
and every resolved path must stay physically inside one of the known safe
roots (Desktop, Documents, Downloads, Pictures, Videos, Music).

The AI (Groq) or fallback parser supplies a *relative* path string
such as "Desktop/College/notes.txt" — never a raw OS path — and this module
is what turns that into a real, validated Path or refuses to.
"""

from pathlib import Path

from app.utils.paths import KNOWN_FOLDERS


class PathSecurityError(Exception):
    """Raised when a requested path is outside the allowed safe roots."""


def resolve_safe_path(path_str: str) -> Path:
    """
    Resolve a spec-style path like "Desktop/College" or
    "Desktop/College/notes.txt" into a validated, absolute Path.

    Raises PathSecurityError for:
      - empty input
      - '..' traversal segments
      - a first segment that isn't a known safe root
      - any resolved path that (after following symlinks/'..') would land
        outside that root
    """
    if not path_str or not path_str.strip():
        raise PathSecurityError("No path was given.")

    raw = path_str.strip().replace("\\", "/")
    segments = [seg for seg in raw.split("/") if seg not in ("", ".")]

    if not segments:
        raise PathSecurityError("No path was given.")

    if any(seg == ".." for seg in segments):
        raise PathSecurityError(f"Path traversal ('..') is not allowed: '{path_str}'")

    root_name = segments[0].lower()
    if root_name not in KNOWN_FOLDERS:
        allowed = ", ".join(sorted(KNOWN_FOLDERS))
        raise PathSecurityError(
            f"'{segments[0]}' is not a safe location. Allowed locations: {allowed}."
        )

    root = KNOWN_FOLDERS[root_name]
    candidate = root.joinpath(*segments[1:]) if len(segments) > 1 else root

    # Resolve both sides (without requiring the path to exist yet — new
    # files/folders won't exist until we create them) and confirm the
    # candidate is still physically inside the root.
    resolved_root = root.resolve()
    resolved_candidate = Path(
        candidate.resolve() if candidate.exists() else _resolve_nonexistent(candidate)
    )

    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        raise PathSecurityError(f"'{path_str}' resolves outside the allowed folder.")

    return resolved_candidate


def _resolve_nonexistent(path: Path) -> Path:
    """
    Path.resolve() works fine on nonexistent paths in modern Python (it just
    won't resolve symlinks it can't see), but we normalize '..'/'.' segments
    manually first via os.path.normpath-equivalent logic to be safe across
    platforms.
    """
    import os
    return Path(os.path.normpath(str(path)))


def split_root_and_name(path_str: str) -> tuple[str, str]:
    """
    Convenience helper: given "Desktop/College", return ("desktop", "College").
    Given just "Desktop", return ("desktop", "").
    Does not validate — call resolve_safe_path for validation.
    """
    raw = path_str.strip().replace("\\", "/")
    segments = [seg for seg in raw.split("/") if seg not in ("", ".")]
    if not segments:
        return "", ""
    root = segments[0].lower()
    name = "/".join(segments[1:])
    return root, name
