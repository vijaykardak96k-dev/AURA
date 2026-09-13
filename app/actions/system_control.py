"""
app/actions/system_control.py

System information (CPU/RAM/battery) via psutil, and Windows power actions
(lock/restart/shutdown/scheduled shutdown/cancel) via the native `shutdown.exe`
and `ctypes` — never via a generic shell command built from AI output.
Every DANGEROUS action here assumes the caller (executor.py) has already
obtained explicit user confirmation.
"""

import ctypes
import subprocess
import sys

import psutil

from app.utils.logger import get_logger

logger = get_logger()


def get_cpu_usage() -> tuple[bool, str]:
    try:
        percent = psutil.cpu_percent(interval=0.5)
        return True, f"You're using about {percent:.0f}% of your CPU."
    except Exception as e:  # psutil is generally safe, but never crash the app
        logger.error(f"get_cpu_usage failed: {e}")
        return False, "I couldn't check CPU usage right now."


def get_ram_usage() -> tuple[bool, str]:
    try:
        mem = psutil.virtual_memory()
        used_gb = mem.used / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        return True, f"You're using about {mem.percent:.0f}% of your RAM ({used_gb:.1f} GB of {total_gb:.1f} GB)."
    except Exception as e:
        logger.error(f"get_ram_usage failed: {e}")
        return False, "I couldn't check RAM usage right now."


def get_battery_status() -> tuple[bool, str]:
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return True, "This machine doesn't report a battery — you're probably on a desktop."
        state = "charging" if battery.power_plugged else "on battery"
        return True, f"Battery is at {battery.percent:.0f}%, currently {state}."
    except Exception as e:
        logger.error(f"get_battery_status failed: {e}")
        return False, "I couldn't check the battery status right now."


def lock_pc() -> tuple[bool, str]:
    if not sys.platform.startswith("win"):
        return False, "Locking the PC is only supported on Windows."
    try:
        ctypes.windll.user32.LockWorkStation()
        return True, "Locking your computer now."
    except Exception as e:
        logger.error(f"lock_pc failed: {e}")
        return False, "I couldn't lock the computer."


def restart_pc() -> tuple[bool, str]:
    """Caller must have already confirmed with the user."""
    if not sys.platform.startswith("win"):
        return False, "Restarting is only supported on Windows."
    try:
        subprocess.run(["shutdown", "/r", "/t", "0"], check=True)
        return True, "Restarting your computer now."
    except (OSError, subprocess.CalledProcessError) as e:
        logger.error(f"restart_pc failed: {e}")
        return False, "I couldn't restart the computer."


def shutdown_pc() -> tuple[bool, str]:
    """Caller must have already confirmed with the user."""
    if not sys.platform.startswith("win"):
        return False, "Shutting down is only supported on Windows."
    try:
        subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
        return True, "Shutting down your computer now."
    except (OSError, subprocess.CalledProcessError) as e:
        logger.error(f"shutdown_pc failed: {e}")
        return False, "I couldn't shut down the computer."


def schedule_shutdown(minutes: int) -> tuple[bool, str]:
    """Caller must have already confirmed with the user."""
    if not sys.platform.startswith("win"):
        return False, "Scheduling a shutdown is only supported on Windows."
    try:
        minutes = max(0, int(minutes))
        seconds = minutes * 60
        subprocess.run(["shutdown", "/s", "/t", str(seconds)], check=True)
        return True, f"Shutdown scheduled in {minutes} minute(s). Say 'cancel shutdown' to stop it."
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        logger.error(f"schedule_shutdown failed: {e}")
        return False, "I couldn't schedule the shutdown."


def cancel_shutdown() -> tuple[bool, str]:
    if not sys.platform.startswith("win"):
        return False, "This is only supported on Windows."
    try:
        subprocess.run(["shutdown", "/a"], check=True)
        return True, "Scheduled shutdown cancelled."
    except subprocess.CalledProcessError:
        # shutdown /a returns nonzero if there was nothing scheduled — not a crash-worthy error.
        return False, "There wasn't a shutdown scheduled."
    except OSError as e:
        logger.error(f"cancel_shutdown failed: {e}")
        return False, "I couldn't cancel the shutdown."
