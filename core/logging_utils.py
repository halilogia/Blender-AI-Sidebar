"""Centralized, privacy-conscious logging for the Blender AI Sidebar."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional


LOGGER_NAME = "blender_ai_sidebar"
LOG_FILENAME = "blender_ai_sidebar.log"
LOG_DIR_ENV = "BLENDER_AI_LOG_DIR"


def get_log_directory() -> str:
    """Return a writable log directory, honoring the optional dev override.

    The project directory is intentionally opt-in through
    ``BLENDER_AI_LOG_DIR``. This prevents an installed extension from trying
    to write beside itself and prevents diagnostic logs from silently entering
    a Git repository.
    """
    requested = os.environ.get(LOG_DIR_ENV, "").strip()
    if requested:
        candidate = Path(os.path.expandvars(os.path.expanduser(requested)))
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except (OSError, ValueError):
            # Fall back to the OS temp directory when the override is stale,
            # read-only, or malformed.
            pass
    return tempfile.gettempdir()


def get_log_path() -> str:
    """Return the diagnostic log path.

    Development example (PowerShell):
        ``$env:BLENDER_AI_LOG_DIR = '.\\logs'``
    """
    return os.path.join(get_log_directory(), LOG_FILENAME)


def get_logger(component: Optional[str] = None) -> logging.Logger:
    """Return the configured addon logger, adding the file handler once."""
    logger_name = f"{LOGGER_NAME}.{component}" if component else LOGGER_NAME
    logger = logging.getLogger(logger_name)
    root_logger = logging.getLogger(LOGGER_NAME)

    if not any(getattr(handler, "_blender_ai_sidebar", False) for handler in root_logger.handlers):
        handler = RotatingFileHandler(
            get_log_path(),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler._blender_ai_sidebar = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        root_logger.propagate = False

    logger.setLevel(logging.INFO)
    logger.propagate = True
    return logger
