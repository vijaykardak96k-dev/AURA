"""
Windows system controls and system information.
"""

import ctypes
import platform
import subprocess
import sys
import psutil
from app.utils.logger import get_logger

logger = get_logger()


def get_cpu_usage():
    try:
        percent = psutil.cpu_percent(interval=0.5)
        return True, f"You're using about {percent:.0f}% of your CPU."
    except Exception as exc:
        logger.error("CPU info failed: %s", exc)
        return False, "I couldn't check CPU usage."


def get_ram_usage():
    try:
        mem = psutil.virtual_memory()
        return True, f"You're using about {mem.percent:.0f}% of your RAM ({mem.used/(1024**3):.1f} GB of {mem.total/(1024**3):.1f} GB)."
    except Exception as exc:
        logger.error("RAM info failed: %s", exc)
        return False, "I couldn't check RAM usage."


def get_disk_usage(drive=None):
    try:
        if not drive:
            drive = (psutil.disk_partitions()[0].mountpoint if psutil.disk_partitions() else "C:\\")
        usage = psutil.disk_usage(drive)
        return True, f"Drive {drive} is {usage.percent:.0f}% full ({usage.free/(1024**3):.1f} GB free of {usage.total/(1024**3):.1f} GB)."
    except Exception as exc:
        logger.error("Disk info failed: %s", exc)
        return False, "I couldn't check disk usage."


def get_battery_status():
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return True, "This computer doesn't report a battery."
        state = "charging" if battery.power_plugged else "on battery"
        return True, f"Battery is at {battery.percent:.0f}%, currently {state}."
    except Exception as exc:
        logger.error("Battery info failed: %s", exc)
        return False, "I couldn't check the battery."


def get_system_info():
    try:
        mem = psutil.virtual_memory()
        return True, (
            f"You're running {platform.system()} {platform.release()} on "
            f"{platform.machine()}. The computer has {psutil.cpu_count(logical=True)} "
            f"logical CPU cores and {mem.total/(1024**3):.1f} GB of RAM."
        )
    except Exception as exc:
        logger.error("System info failed: %s", exc)
        return False, "I couldn't collect system information."


def lock_pc():
    if not sys.platform.startswith("win"):
        return False, "Locking is only supported on Windows."
    try:
        ctypes.windll.user32.LockWorkStation()
        return True, "Locking your computer now."
    except Exception as exc:
        logger.error("lock_pc failed: %s", exc)
        return False, "I couldn't lock the computer."


def sleep_pc():
    if not sys.platform.startswith("win"):
        return False, "Sleep is only supported on Windows."
    try:
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True)
        return True, "Putting your computer to sleep."
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("sleep_pc failed: %s", exc)
        return False, "I couldn't put the computer to sleep."


def restart_pc():
    if not sys.platform.startswith("win"):
        return False, "Restarting is only supported on Windows."
    try:
        subprocess.run(["shutdown", "/r", "/t", "0"], check=True)
        return True, "Restarting your computer now."
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.error("restart_pc failed: %s", exc)
        return False, "I couldn't restart the computer."


def shutdown_pc():
    if not sys.platform.startswith("win"):
        return False, "Shutting down is only supported on Windows."
    try:
        subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
        return True, "Shutting down your computer now."
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.error("shutdown_pc failed: %s", exc)
        return False, "I couldn't shut down the computer."


def schedule_shutdown(minutes):
    if not sys.platform.startswith("win"):
        return False, "Scheduling shutdown is only supported on Windows."
    try:
        minutes = max(0, int(minutes))
        subprocess.run(["shutdown", "/s", "/t", str(minutes * 60)], check=True)
        return True, f"Shutdown scheduled in {minutes} minute(s). Say cancel shutdown to stop it."
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        logger.error("schedule_shutdown failed: %s", exc)
        return False, "I couldn't schedule the shutdown."


def cancel_shutdown():
    if not sys.platform.startswith("win"):
        return False, "This is only supported on Windows."
    try:
        subprocess.run(["shutdown", "/a"], check=True)
        return True, "Scheduled shutdown cancelled."
    except subprocess.CalledProcessError:
        return False, "There wasn't a shutdown scheduled."
    except OSError as exc:
        logger.error("cancel_shutdown failed: %s", exc)
        return False, "I couldn't cancel the shutdown."
