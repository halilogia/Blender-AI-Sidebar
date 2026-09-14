"""Centralized, privacy-conscious logging for the Blender AI Sidebar."""

from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from typing import Optional


LOGGER_NAME = "blender_ai_sidebar"
LOG_FILENAME = "blender_ai_sidebar.log"


def get_log_path() -> str:
    """Return the user-local diagnostic log path."""
    return os.path.join(tempfile.gettempdir(), LOG_FILENAME)


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
