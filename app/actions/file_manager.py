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
