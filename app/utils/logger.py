"""
app/utils/logger.py

Central logging configuration. Writes to logs/aura.log and to console.
Callers must never pass API keys, passwords, or raw file contents into
log messages — only paths, action names, and short result summaries.
"""

import logging
from logging.handlers import RotatingFileHandler
from app.utils.paths import LOGS_DIR, ensure_project_dirs

_LOGGER_NAME = "aura"
_initialized = False


def get_logger() -> logging.Logger:
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)

    if _initialized:
        return logger

    ensure_project_dirs()
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_file = LOGS_DIR / "aura.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    _initialized = True
    return logger
