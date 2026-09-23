"""
Validated AURA action dispatcher.
"""

from typing import Callable
from app.actions import (
    app_control,
    browser,
    clipboard,
    file_manager,
    media,
    screenshot,
    system_control,
)
from app.brain.schemas import ActionItem
from app.memory import memory_manager
from app.utils.paths import KNOWN_FOLDERS
from app.utils.logger import get_logger

logger = get_logger()


def _open_app(p): return app_control.open_app(p["app"])
def _close_app(p): return app_control.close_app(p["app"])
def _restart_app(p): return app_control.restart_app(p["app"])
def _list_running_apps(p): return app_control.list_running_apps()
def _list_installed_apps(p): return app_control.list_installed_apps(str(p.get("query", "") or ""))
def _find_app(p): return app_control.find_app(p["app"])

def _resolve_contextual_path(p):
    """
    Resolve "that screenshot"/"that folder" style references to a real
    path using the last target AURA created or opened. Returns the raw
    path unchanged when there is no context to apply.
    """
    path = str(p.get("path", "") or "").strip()
    context = str(p.get("context", "") or "").strip()

    if path:
        return path

    if context == "last_screenshot":
        return "screenshots"

    last = memory_manager.get_last_target() or {}
    return str(last.get("path", "") or "")


def _open_folder(p):
    path = _resolve_contextual_path(p)
    if not path:
        return False, "Which folder should I open?"
    return file_manager.open_folder(path)


def _open_file(p):
    path = _resolve_contextual_path(p)
    if not path:
        return False, "Which file should I open?"
    if str(p.get("context", "")) == "last_screenshot":
        return file_manager.open_latest_file("screenshots")
    return file_manager.open_file(path)
def _list_files(p): return file_manager.list_files(p["path"], bool(p.get("show_hidden", False)))
def _create_folder(p): return file_manager.create_folder(p["path"])
def _create_file(p): return file_manager.create_file(p["path"], p.get("content", ""))
def _write_file(p): return file_manager.write_file(p["path"], p["content"], bool(p.get("append", False)))
def _rename_file(p): return file_manager.rename_file(p["path"], p["new_name"])
def _move_file(p): return file_manager.move_file(p["path"], p["destination"])
def _copy_file(p): return file_manager.copy_file(p["path"], p["destination"])
def _delete_file(p): return file_manager.delete_file(p["path"])
def _search_files(p): return file_manager.search_files(p["query"], p.get("path"))
def _find_files_by_type(p): return file_manager.find_files_by_type(p.get("keyword", ""), p.get("extension", ""), p.get("path"))
def _list_recent_files(p): return file_manager.list_recently_modified(p.get("path"), float(p.get("hours", 24)))
def _open_latest_file(p): return file_manager.open_latest_file(p["path"])

def _screenshot(p): return screenshot.take_screenshot()
def _open_screenshots(p): return file_manager.open_folder("Pictures/Screenshots")

def _cpu(p): return system_control.get_cpu_usage()
def _ram(p): return system_control.get_ram_usage()
def _disk(p): return system_control.get_disk_usage(p.get("drive"))
def _battery(p): return system_control.get_battery_status()
def _system(p): return system_control.get_system_info()

def _website(p): return browser.open_website(p["site"], p.get("browser"))
def _search(p): return browser.web_search(p["query"], p.get("browser"))
def _youtube(p): return browser.youtube_search(p["query"])
def _youtube_play(p): return browser.youtube_play(p["query"])
def _open_brave(p): return app_control.open_app("brave")

def _lock(p): return system_control.lock_pc()
def _sleep(p): return system_control.sleep_pc()
def _restart(p): return system_control.restart_pc()
def _shutdown(p): return system_control.shutdown_pc()
def _schedule(p): return system_control.schedule_shutdown(int(p["minutes"]))
def _cancel(p): return system_control.cancel_shutdown()

def _play_pause(p): return media.play_pause()
def _volume_up(p): return media.volume_up(p.get("steps", 2))
def _volume_down(p): return media.volume_down(p.get("steps", 2))
def _mute(p): return media.volume_mute()

def _remember(p): return memory_manager.remember(p["key"], p["value"])
def _recall(p): return memory_manager.recall_as_message(p["key"])
def _forget(p): return memory_manager.forget(p["key"])
def _clipboard_read(p): return clipboard.read_clipboard()
def _clipboard_write(p): return clipboard.write_clipboard(p["text"])
def _help(p):
    from app.brain.identity import HELP_TEXT
    return True, HELP_TEXT
def _converse(p): return True, p["text"]
def _activity_summary(p): return memory_manager.summarize_period(p.get("period", "today"))
def _unknown(p): return False, "I don't understand that command yet."


_DISPATCH: dict[str, Callable[[dict], tuple[bool, str]]] = {
    "OPEN_APP": _open_app, "CLOSE_APP": _close_app, "RESTART_APP": _restart_app,
    "LIST_RUNNING_APPS": _list_running_apps,
    "LIST_INSTALLED_APPS": _list_installed_apps, "FIND_APP": _find_app,
    "OPEN_FOLDER": _open_folder, "OPEN_FILE": _open_file, "LIST_FILES": _list_files,
    "CREATE_FOLDER": _create_folder, "CREATE_FILE": _create_file,
    "WRITE_FILE": _write_file, "RENAME_FILE": _rename_file,
    "MOVE_FILE": _move_file, "COPY_FILE": _copy_file, "DELETE_FILE": _delete_file,
    "SEARCH_FILES": _search_files,
    "FIND_FILES_BY_TYPE": _find_files_by_type, "LIST_RECENT_FILES": _list_recent_files,
    "OPEN_LATEST_FILE": _open_latest_file,
    "SCREENSHOT": _screenshot, "OPEN_SCREENSHOTS": _open_screenshots,
    "CPU_INFO": _cpu, "RAM_INFO": _ram, "DISK_INFO": _disk,
    "BATTERY_INFO": _battery, "SYSTEM_INFO": _system,
    "OPEN_WEBSITE": _website, "WEB_SEARCH": _search, "YOUTUBE_SEARCH": _youtube, "YOUTUBE_PLAY": _youtube_play, "OPEN_BRAVE": _open_brave,
    "LOCK_SYSTEM": _lock, "SLEEP_SYSTEM": _sleep,
    "RESTART_SYSTEM": _restart, "SHUTDOWN_SYSTEM": _shutdown,
    "SCHEDULE_SHUTDOWN": _schedule, "CANCEL_SHUTDOWN": _cancel,
    "MEDIA_PLAY_PAUSE": _play_pause, "VOLUME_UP": _volume_up,
    "VOLUME_DOWN": _volume_down, "VOLUME_MUTE": _mute,
    "REMEMBER": _remember, "RECALL": _recall, "FORGET": _forget,
    "CLIPBOARD_READ": _clipboard_read, "CLIPBOARD_WRITE": _clipboard_write,
    "HELP": _help,
    "CONVERSE": _converse, "UNKNOWN": _unknown, "ACTIVITY_SUMMARY": _activity_summary,
}


def execute_action(action: ActionItem) -> tuple[bool, str]:
    if not action.valid:
        message = action.error or "That action couldn't be validated."
        memory_manager.log_action(action.action, action.action, False, message)
        return False, message

    handler = _DISPATCH.get(action.action, _unknown)
    try:
        success, message = handler(action.parameters)
    except KeyError as exc:
        success, message = False, f"That action is missing a required detail: {exc}"
    except Exception:
        logger.exception("execute_action failed for %s", action.action)
        success, message = False, "Something went wrong while doing that. Check the logs."

    memory_manager.log_action(action.action, action.action, success, message)

    if success and action.action in ("CREATE_FOLDER", "CREATE_FILE") and "path" in action.parameters:
        memory_manager.set_last_target({"path": action.parameters["path"]})

    return success, message
