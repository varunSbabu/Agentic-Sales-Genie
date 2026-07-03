"""Loguru-based logging configured from settings."""

import sys

from loguru import logger

from backend.config import settings


def configure_logging() -> None:
    """Replace default Loguru handler with a stderr handler at configured level."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}:{function}:{line}</cyan> "
            "- <level>{message}</level>"
        ),
        backtrace=True,
        diagnose=False,
    )


__all__ = ["configure_logging", "logger"]
