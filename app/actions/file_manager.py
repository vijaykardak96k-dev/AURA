"""
Safe-root file and folder operations.
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
MAX_LIST_ITEMS = 40


def open_folder(path):
    try:
        target = resolve_safe_path(path)
        if not target.exists():
            return False, f"I couldn't find '{path}'."
        os.startfile(str(target))
        return True, f"Opening {path}."
    except (PathSecurityError, OSError, AttributeError) as exc:
        logger.error("open_folder failed: %s", exc)
        return False, f"I couldn't open '{path}'."


def open_file(path):
    try:
        target = resolve_safe_path(path)
        if not target.exists():
            return False, f"I couldn't find '{path}'."
        if not target.is_file():
            return False, f"'{path}' isn't a file."
        os.startfile(str(target))
        return True, f"Opening {path}."
    except (PathSecurityError, OSError, AttributeError) as exc:
        logger.error("open_file failed: %s", exc)
        return False, f"I couldn't open '{path}'."


def list_files(path, show_hidden=False):
    try:
        target = resolve_safe_path(path)
        if not target.is_dir():
            return False, f"'{path}' isn't a folder."
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if not show_hidden and item.name.startswith("."):
                continue
            suffix = "/" if item.is_dir() else ""
            entries.append(item.name + suffix)
            if len(entries) >= MAX_LIST_ITEMS:
                break
        if not entries:
            return True, f"'{path}' is empty."
        extra = " (showing the first 40)" if len(list(target.iterdir())) > MAX_LIST_ITEMS else ""
        return True, f"Contents of {path}: " + ", ".join(entries) + extra + "."
    except (PathSecurityError, OSError) as exc:
        logger.error("list_files failed: %s", exc)
        return False, f"I couldn't list '{path}'."


def create_folder(path):
    try:
        target = resolve_safe_path(path)
        if target.exists():
            return False, f"A folder already exists at '{path}'."
        target.mkdir(parents=True, exist_ok=False)
        return True, f"Created folder '{path}'."
    except (PathSecurityError, OSError) as exc:
        logger.error("create_folder failed: %s", exc)
        return False, f"I couldn't create the folder '{path}'."


def create_file(path, content=""):
    if Path(path).suffix.lower() not in ALLOWED_EXTENSIONS:
        return False, "I can only create .txt, .md, .json, .csv, and .py files."
    try:
        target = resolve_safe_path(path)
        if target.exists():
            return False, f"A file already exists at '{path}'."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
        return True, f"Created file '{path}'."
    except (PathSecurityError, OSError) as exc:
        logger.error("create_file failed: %s", exc)
        return False, f"I couldn't create '{path}'."


def write_file(path, content, append=False):
    try:
        target = resolve_safe_path(path)
        if not target.exists():
            return False, f"I couldn't find '{path}' to write to."
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as handle:
            if append and target.stat().st_size:
                handle.write("\n")
            handle.write(content)
        return True, f"Updated '{path}'."
    except (PathSecurityError, OSError) as exc:
        logger.error("write_file failed: %s", exc)
        return False, f"I couldn't write to '{path}'."


def rename_file(path, new_name):
    if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        return False, "The new name must be a plain file or folder name."
    try:
        src = resolve_safe_path(path)
        dst = src.parent / new_name
        if not src.exists():
            return False, f"I couldn't find '{path}'."
        if dst.exists():
            return False, f"'{new_name}' already exists."
        src.rename(dst)
        return True, f"Renamed '{path}' to '{new_name}'."
    except (PathSecurityError, OSError) as exc:
        logger.error("rename_file failed: %s", exc)
        return False, f"I couldn't rename '{path}'."


def move_file(path, destination):
    try:
        src = resolve_safe_path(path)
        dest = resolve_safe_path(destination)
        dst = dest / src.name
        if not src.exists():
            return False, f"I couldn't find '{path}'."
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return True, f"Moved '{path}' to {destination}."
    except (PathSecurityError, OSError, shutil.Error) as exc:
        logger.error("move_file failed: %s", exc)
        return False, f"I couldn't move '{path}'."


def copy_file(path, destination):
    try:
        src = resolve_safe_path(path)
        dest = resolve_safe_path(destination)
        dst = dest / src.name
        if not src.exists():
            return False, f"I couldn't find '{path}'."
        dest.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=False)
        else:
            shutil.copy2(src, dst)
        return True, f"Copied '{path}' to {destination}."
    except FileExistsError:
        return False, "A file or folder with that name already exists at the destination."
    except (PathSecurityError, OSError, shutil.Error) as exc:
        logger.error("copy_file failed: %s", exc)
        return False, f"I couldn't copy '{path}'."


def delete_file(path):
    try:
        target = resolve_safe_path(path)
        if not target.exists():
            return False, f"I couldn't find '{path}'."
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True, f"Deleted '{path}'."
    except (PathSecurityError, OSError) as exc:
        logger.error("delete_file failed: %s", exc)
        return False, f"I couldn't delete '{path}'."


def search_files(query, path=None):
    if path:
        try:
            roots = [resolve_safe_path(path)]
        except PathSecurityError as exc:
            return False, str(exc)
    else:
        roots = [KNOWN_FOLDERS[r] for r in SEARCH_ROOTS]

    matches = []
    q = query.lower()
    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.rglob("*"):
                if q in item.name.lower():
                    matches.append(str(item))
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        except OSError as exc:
            logger.warning("search_files error: %s", exc)
        if len(matches) >= MAX_SEARCH_RESULTS:
            break

    if not matches:
        return False, f"I couldn't find anything matching '{query}'."
    preview = "; ".join(Path(m).name for m in matches[:5])
    more = f" and {len(matches)-5} more" if len(matches) > 5 else ""
    return True, f"Found {len(matches)} match(es): {preview}{more}."


# --------------------------------------------------------------------
# Natural-language file intelligence: find by type, list what changed
# recently, and jump straight to the newest file in a folder.
# --------------------------------------------------------------------

# Spoken/typed file-type words mapped to real extensions, so "find my
# kubernetes yaml files" or "show me my python scripts" resolve without the
# user having to say ".yaml" or ".py" out loud.
_EXTENSION_ALIASES = {
    "yaml": [".yaml", ".yml"],
    "yml": [".yaml", ".yml"],
    "json": [".json"],
    "text": [".txt"],
    "txt": [".txt"],
    "markdown": [".md"],
    "python": [".py"],
    "csv": [".csv"],
    "spreadsheet": [".csv", ".xlsx"],
    "excel": [".xlsx", ".xls"],
    "word": [".docx", ".doc"],
    "document": [".docx", ".doc", ".pdf", ".txt"],
    "pdf": [".pdf"],
    "image": [".png", ".jpg", ".jpeg", ".gif", ".bmp"],
    "picture": [".png", ".jpg", ".jpeg", ".gif", ".bmp"],
    "photo": [".png", ".jpg", ".jpeg", ".gif", ".bmp"],
    "screenshot": [".png", ".jpg", ".jpeg"],
    "video": [".mp4", ".mov", ".avi", ".mkv"],
    "zip": [".zip"],
    "archive": [".zip", ".rar", ".7z"],
}


def find_files_by_type(keyword: str = "", extension: str = "", path=None):
    """Find files matching an extension/type word and an optional keyword,
    e.g. find_files_by_type("kubernetes", "yaml") for "find my kubernetes
    yaml files".
    """
    if path:
        try:
            roots = [resolve_safe_path(path)]
        except PathSecurityError as exc:
            return False, str(exc)
    else:
        roots = [KNOWN_FOLDERS[r] for r in SEARCH_ROOTS]

    ext_word = extension.strip().lower().lstrip(".")
    wanted_extensions = _EXTENSION_ALIASES.get(ext_word)
    if not wanted_extensions and ext_word:
        wanted_extensions = [f".{ext_word}"]

    keyword_lower = keyword.strip().lower()

    matches = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue
                if wanted_extensions and item.suffix.lower() not in wanted_extensions:
                    continue
                if keyword_lower and keyword_lower not in item.name.lower():
                    continue
                matches.append(item)
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        except OSError as exc:
            logger.warning("find_files_by_type error: %s", exc)
        if len(matches) >= MAX_SEARCH_RESULTS:
            break

    if not matches:
        what = f"'{keyword}' " if keyword else ""
        kind = f"{ext_word} " if ext_word else ""
        return False, f"I couldn't find any {kind}{what}files."

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    preview = "; ".join(m.name for m in matches[:5])
    more = f" and {len(matches)-5} more" if len(matches) > 5 else ""
    return True, f"Found {len(matches)} file(s): {preview}{more}."


def list_recently_modified(path=None, hours: float = 24):
    """List files modified within the last `hours` hours — for "what did I
    modify today"-style requests.
    """
    import time as _time

    if path:
        try:
            roots = [resolve_safe_path(path)]
        except PathSecurityError as exc:
            return False, str(exc)
    else:
        roots = [KNOWN_FOLDERS[r] for r in SEARCH_ROOTS]

    cutoff = _time.time() - (hours * 3600)
    matches = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    if item.stat().st_mtime >= cutoff:
                        matches.append(item)
                except OSError:
                    continue
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        except OSError as exc:
            logger.warning("list_recently_modified error: %s", exc)
        if len(matches) >= MAX_SEARCH_RESULTS:
            break

    if not matches:
        period = "today" if hours <= 24 else f"the last {int(hours)} hours"
        return True, f"I didn't find any files modified {period}."

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    preview = "; ".join(m.name for m in matches[:8])
    more = f" and {len(matches)-8} more" if len(matches) > 8 else ""
    return True, f"Modified recently: {preview}{more}."


def open_latest_file(path):
    """Open the most recently modified file inside a folder (e.g. the
    latest screenshot in Pictures/Screenshots).
    """
    try:
        target = resolve_safe_path(path)
        if not target.is_dir():
            return False, f"'{path}' isn't a folder."
        files = [f for f in target.iterdir() if f.is_file()]
        if not files:
            return False, f"'{path}' doesn't have any files yet."
        latest = max(files, key=lambda f: f.stat().st_mtime)
        os.startfile(str(latest))
        return True, f"Opening the latest file in {path}: {latest.name}."
    except (PathSecurityError, OSError, AttributeError) as exc:
        logger.error("open_latest_file failed: %s", exc)
        return False, f"I couldn't open the latest file in '{path}'."
