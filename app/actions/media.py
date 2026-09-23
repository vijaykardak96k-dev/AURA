"""Windows media-key controls."""

import ctypes
import sys
from app.utils.logger import get_logger

logger = get_logger()

_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_VK_MEDIA_PLAY_PAUSE = 0xB3
_KEYEVENTF_KEYUP = 0x0002


def _press(vk):
    if not sys.platform.startswith("win"):
        return False, "Media controls are only supported on Windows."
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        return True, None
    except Exception as exc:
        logger.error("media key failed: %s", exc)
        return False, str(exc)


def play_pause():
    ok, _ = _press(_VK_MEDIA_PLAY_PAUSE)
    return (True, "Toggled play and pause.") if ok else (False, "I couldn't control media.")


def volume_up(steps=2):
    try:
        steps = max(1, min(20, int(steps)))
    except (TypeError, ValueError):
        steps = 2
    for _ in range(steps):
        _press(_VK_VOLUME_UP)
    return True, "Volume increased."


def volume_down(steps=2):
    try:
        steps = max(1, min(20, int(steps)))
    except (TypeError, ValueError):
        steps = 2
    for _ in range(steps):
        _press(_VK_VOLUME_DOWN)
    return True, "Volume decreased."


def volume_mute():
    ok, _ = _press(_VK_VOLUME_MUTE)
    return (True, "Mute toggled.") if ok else (False, "I couldn't control the volume.")
