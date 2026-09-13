"""
app/actions/screenshot.py

Captures the screen with `mss` and saves it into the configurable
screenshots directory, with a timestamped filename.
"""

from datetime import datetime

from app.utils.logger import get_logger
from app.utils.paths import DEFAULT_SCREENSHOT_DIR, ensure_project_dirs

logger = get_logger()


def take_screenshot(output_dir=None) -> tuple[bool, str]:
    ensure_project_dirs()
    out_dir = output_dir or DEFAULT_SCREENSHOT_DIR

    try:
        import mss  # imported lazily so the rest of the app works even if
                     # mss isn't installed / fails to load on a headless box
    except ImportError:
        return False, "The screenshot library isn't installed. Run: pip install mss"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = out_dir / f"screenshot_{timestamp}.png"

        with mss.mss() as sct:
            sct.shot(output=str(filepath))

        logger.info(f"Screenshot saved: {filepath}")
        return True, f"Screenshot saved to {filepath}."
    except Exception as e:
        logger.error(f"take_screenshot failed: {e}")
        return False, "I couldn't take a screenshot."
