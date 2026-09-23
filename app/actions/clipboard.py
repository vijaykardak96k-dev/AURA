"""
Clipboard read/write.

Uses only what Windows already ships (`clip.exe` for writing, PowerShell's
Get-Clipboard for reading) so there's no extra dependency. Both calls pass
fixed argument lists — the text goes in over stdin, never interpolated
into a command string.
"""

import subprocess
import sys

from app.utils.logger import get_logger

logger = get_logger()

_IS_WINDOWS = sys.platform.startswith("win")

MAX_CLIPBOARD_CHARS = 4000


def read_clipboard() -> tuple[bool, str]:
    if not _IS_WINDOWS:
        return False, "Clipboard access is only supported on Windows."

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("read_clipboard failed: %s", exc)
        return False, "I couldn't read the clipboard."

    text = (result.stdout or "").strip()

    if not text:
        return True, "The clipboard is empty."

    if len(text) > MAX_CLIPBOARD_CHARS:
        text = text[:MAX_CLIPBOARD_CHARS] + " …"

    return True, f"The clipboard contains: {text}"


def write_clipboard(text: str) -> tuple[bool, str]:
    if not _IS_WINDOWS:
        return False, "Clipboard access is only supported on Windows."

    text = "" if text is None else str(text)

    if len(text) > MAX_CLIPBOARD_CHARS:
        return False, "That's too long for me to put on the clipboard."

    try:
        subprocess.run(
            ["clip.exe"],
            input=text,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("write_clipboard failed: %s", exc)
        return False, "I couldn't copy that to the clipboard."

    return True, "Copied to your clipboard."
