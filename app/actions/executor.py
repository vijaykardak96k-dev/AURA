"""
app/actions/executor.py

The ONLY place that turns a validated ActionItem into a real function call
against app/actions/{file_manager,app_control,system_control,browser,screenshot}.py
or app/memory/memory_manager.py.

By the time execute_action() is called, the action has already been:
  1. schema-validated (app/brain/schemas.py)
  2. security-classified with requires_confirmation forced correctly
     (app/security/permissions.py)
  3. confirmed by the user if action.requires_confirmation was True
     (the caller — main.py / future UI — is responsible for step 3
     BEFORE calling execute_action; this module trusts that gate)

This module still never accepts a raw shell command, arbitrary path, or
anything not already constrained by the whitelist + path-safety layer —
it is a dispatch table, not a general executor.
"""

from typing import Callable

from app.actions import app_control, browser, file_manager, screenshot, system_control
from app.brain.schemas import ActionItem
from app.memory import memory_manager
from app.utils.logger import get_logger

logger = get_logger()


def _run_open_app(p: dict) -> tuple[bool, str]:
    return app_control.open_app(p["app"])


def _run_close_app(p: dict) -> tuple[bool, str]:
    return app_control.close_app(p["app"])


def _run_open_folder(p: dict) -> tuple[bool, str]:
    return file_manager.open_folder(p["path"])


def _run_create_folder(p: dict) -> tuple[bool, str]:
    return file_manager.create_folder(p["path"])


def _run_create_file(p: dict) -> tuple[bool, str]:
    return file_manager.create_file(p["path"], p.get("content", ""))


def _run_write_file(p: dict) -> tuple[bool, str]:
    return file_manager.write_file(p["path"], p["content"], append=bool(p.get("append", False)))


def _run_rename_file(p: dict) -> tuple[bool, str]:
    return file_manager.rename_file(p["path"], p["new_name"])


def _run_move_file(p: dict) -> tuple[bool, str]:
    return file_manager.move_file(p["path"], p["destination"])


def _run_delete_file(p: dict) -> tuple[bool, str]:
    return file_manager.delete_file(p["path"])


def _run_search_files(p: dict) -> tuple[bool, str]:
    return file_manager.search_files(p["query"], p.get("path"))


def _run_screenshot(p: dict) -> tuple[bool, str]:
    return screenshot.take_screenshot()


def _run_cpu_info(p: dict) -> tuple[bool, str]:
    return system_control.get_cpu_usage()


def _run_ram_info(p: dict) -> tuple[bool, str]:
    return system_control.get_ram_usage()


def _run_battery_info(p: dict) -> tuple[bool, str]:
    return system_control.get_battery_status()


def _run_open_website(p: dict) -> tuple[bool, str]:
    return browser.open_website(p["site"])


def _run_web_search(p: dict) -> tuple[bool, str]:
    return browser.web_search(p["query"])


def _run_lock_system(p: dict) -> tuple[bool, str]:
    return system_control.lock_pc()


def _run_restart_system(p: dict) -> tuple[bool, str]:
    return system_control.restart_pc()


def _run_shutdown_system(p: dict) -> tuple[bool, str]:
    return system_control.shutdown_pc()


def _run_schedule_shutdown(p: dict) -> tuple[bool, str]:
    return system_control.schedule_shutdown(int(p["minutes"]))


def _run_cancel_shutdown(p: dict) -> tuple[bool, str]:
    return system_control.cancel_shutdown()


def _run_remember(p: dict) -> tuple[bool, str]:
    return memory_manager.remember(p["key"], p["value"])


def _run_recall(p: dict) -> tuple[bool, str]:
    return memory_manager.recall_as_message(p["key"])


def _run_forget(p: dict) -> tuple[bool, str]:
    return memory_manager.forget(p["key"])


def _run_converse(p: dict) -> tuple[bool, str]:
    # Not a desktop action — just relays the natural-language reply the AI
    # provider (or the small fallback-parser greeting set) already
    # produced. Nothing here touches the filesystem, an app, or the OS.
    return True, p["text"]


def _run_unknown(p: dict) -> tuple[bool, str]:
    return False, "I don't understand that command yet."


_DISPATCH: dict[str, Callable[[dict], tuple[bool, str]]] = {
    "OPEN_APP": _run_open_app,
    "CLOSE_APP": _run_close_app,
    "OPEN_FOLDER": _run_open_folder,
    "CREATE_FOLDER": _run_create_folder,
    "CREATE_FILE": _run_create_file,
    "WRITE_FILE": _run_write_file,
    "RENAME_FILE": _run_rename_file,
    "MOVE_FILE": _run_move_file,
    "DELETE_FILE": _run_delete_file,
    "SEARCH_FILES": _run_search_files,
    "SCREENSHOT": _run_screenshot,
    "CPU_INFO": _run_cpu_info,
    "RAM_INFO": _run_ram_info,
    "BATTERY_INFO": _run_battery_info,
    "OPEN_WEBSITE": _run_open_website,
    "WEB_SEARCH": _run_web_search,
    "LOCK_SYSTEM": _run_lock_system,
    "RESTART_SYSTEM": _run_restart_system,
    "SHUTDOWN_SYSTEM": _run_shutdown_system,
    "SCHEDULE_SHUTDOWN": _run_schedule_shutdown,
    "CANCEL_SHUTDOWN": _run_cancel_shutdown,
    "REMEMBER": _run_remember,
    "RECALL": _run_recall,
    "FORGET": _run_forget,
    "CONVERSE": _run_converse,
    "UNKNOWN": _run_unknown,
}


def execute_action(action: ActionItem) -> tuple[bool, str]:
    """
    Execute one already-validated, already-confirmed ActionItem. Returns
    (success, message). Never raises — any unexpected exception from the
    underlying action module is caught here so a single bad action can
    never take down the whole app (spec: "must never crash").

    Also records the action to the SQLite action log via memory_manager,
    and — for CREATE_FOLDER / CREATE_FILE — updates the "last target" used
    for resolving 'it'/'that' in a follow-up command.
    """
    if not action.valid:
        message = action.error or "That action couldn't be validated."
        memory_manager.log_action(action.action, action.action, False, message)
        return False, message

    handler = _DISPATCH.get(action.action, _run_unknown)

    try:
        success, message = handler(action.parameters)
    except KeyError as e:
        message = f"That action is missing a required detail: {e}"
        logger.error(f"execute_action KeyError for {action.action}: {e}")
        success = False
    except Exception as e:  # last-resort safety net — never propagate
        logger.error(f"execute_action failed unexpectedly for {action.action}: {e}")
        success, message = False, "Something went wrong while doing that. Check the logs for details."

    memory_manager.log_action(action.action, action.action, success, message)

    if success and action.action in ("CREATE_FOLDER", "CREATE_FILE") and "path" in action.parameters:
        memory_manager.set_last_target({"path": action.parameters["path"]})

    return success, message
