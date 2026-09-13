"""
app/actions/file_manager.py

All file/folder operations. Every function takes spec-style safe-root
paths (e.g. "Desktop/College/notes.txt") and resolves them through
app/utils/path_safety.py BEFORE touching disk — so path traversal and
access outside the known safe roots is rejected before any OS call.

Every function returns (success: bool, message: str) and NEVER raises to
the caller — every OS-level error is caught and turned into a friendly
message, per the "must never crash" requirement.

Only .txt, .md, .json, .csv, .py files may be created (a whitelist of safe
text extensions), matching the spec's initial supported file types.
"""

import os
import shutil
from pathlib import Path

from app.utils.logger import get_logger
from app.utils.path_safety import PathSecurityError, resolve_safe_path
from app.utils.paths import KNOWN_FOLDERS

logger = get_logger()

ALLOWED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".py"}
SEARCH_ROOTS = ["desktop", "documents", "downloads"]
MAX_SEARCH_RESULTS = 20


def open_folder(path: str) -> tuple[bool, str]:
    try:
        target = resolve_safe_path(path)
    except PathSecurityError as e:
        return False, str(e)

    try:
        if not target.exists():
            return False, f"I couldn't find '{path}'."
        os.startfile(str(target))  # Windows-only; see README note
        return True, f"Opening {path}."
    except AttributeError:
        return False, "Opening folders directly is only supported on Windows."
    except OSError as e:
        logger.error(f"open_folder failed: {e}")
        return False, "I ran into a problem opening that folder."


def create_folder(path: str) -> tuple[bool, str]:
    try:
        target = resolve_safe_path(path)
    except PathSecurityError as e:
        return False, str(e)

    try:
        if target.exists():
            return False, f"A folder already exists at '{path}'."
        target.mkdir(parents=True, exist_ok=False)
        logger.info(f"Created folder: {target}")
        return True, f"Created folder '{path}'."
    except OSError as e:
        logger.error(f"create_folder failed: {e}")
        return False, f"I couldn't create the folder '{path}'."


def create_file(path: str, content: str = "") -> tuple[bool, str]:
    ext = Path(path).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"I can only create these file types: {', '.join(sorted(ALLOWED_EXTENSIONS))}."

    try:
        target = resolve_safe_path(path)
    except PathSecurityError as e:
        return False, str(e)

    try:
        if target.exists():
            return False, f"A file already exists at '{path}'."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
        logger.info(f"Created file: {target}")
        return True, f"Created file '{path}'."
    except OSError as e:
        logger.error(f"create_file failed: {e}")
        return False, f"I couldn't create the file '{path}'."


def write_file(path: str, content: str, append: bool = False) -> tuple[bool, str]:
    try:
        target = resolve_safe_path(path)
    except PathSecurityError as e:
        return False, str(e)

    try:
        if not target.exists():
            return False, f"I couldn't find '{path}' to write to."
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as f:
            if append and target.stat().st_size > 0:
                f.write("\n")
            f.write(content)
        return True, f"Updated '{path}'."
    except OSError as e:
        logger.error(f"write_file failed: {e}")
        return False, f"I couldn't write to '{path}'."


def rename_file(path: str, new_name: str) -> tuple[bool, str]:
    # new_name must be a bare filename, never a path — this blocks a
    # traversal attempt smuggled in through new_name (e.g. "../../evil").
    if "/" in new_name or "\\" in new_name or new_name in ("..", "."):
        return False, "The new name can't contain a path — just give a plain file or folder name."

    try:
        src = resolve_safe_path(path)
    except PathSecurityError as e:
        return False, str(e)

    dst = src.parent / new_name

    try:
        if not src.exists():
            return False, f"I couldn't find '{path}'."
        if dst.exists():
            return False, f"'{new_name}' already exists — I won't overwrite it."
        src.rename(dst)
        return True, f"Renamed '{path}' to '{new_name}'."
    except OSError as e:
        logger.error(f"rename_file failed: {e}")
        return False, f"I couldn't rename '{path}'."


def move_file(path: str, destination: str) -> tuple[bool, str]:
    try:
        src = resolve_safe_path(path)
        dest_folder = resolve_safe_path(destination)
    except PathSecurityError as e:
        return False, str(e)

    dst = dest_folder / src.name
    try:
        if not src.exists():
            return False, f"I couldn't find '{path}'."
        dest_folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return True, f"Moved '{path}' to {destination}."
    except (OSError, shutil.Error) as e:
        logger.error(f"move_file failed: {e}")
        return False, f"I couldn't move '{path}'."


def delete_file(path: str) -> tuple[bool, str]:
    """
    Caller (executor) MUST have already obtained user confirmation before
    calling this — this function performs the delete unconditionally.
    """
    try:
        target = resolve_safe_path(path)
    except PathSecurityError as e:
        return False, str(e)

    try:
        if not target.exists():
            return False, f"I couldn't find '{path}'."
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        logger.info(f"Deleted: {target}")
        return True, f"Deleted '{path}'."
    except OSError as e:
        logger.error(f"delete_file failed: {e}")
        return False, f"I couldn't delete '{path}'."


def search_files(query: str, path: str | None = None) -> tuple[bool, str]:
    if path:
        try:
            roots = [resolve_safe_path(path)]
        except PathSecurityError as e:
            return False, str(e)
    else:
        roots = [KNOWN_FOLDERS[r] for r in SEARCH_ROOTS]

    matches: list[str] = []
    q = query.lower()

    for root in roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if q in p.name.lower():
                    matches.append(str(p))
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        except OSError as e:
            logger.warning(f"search_files error under {root}: {e}")
        if len(matches) >= MAX_SEARCH_RESULTS:
            break

    if not matches:
        return False, f"I couldn't find anything matching '{query}'."

    preview = "; ".join(Path(m).name for m in matches[:5])
    more = f" and {len(matches) - 5} more" if len(matches) > 5 else ""
    return True, f"Found {len(matches)} match(es): {preview}{more}."
