"""Logging configuration using loguru with colorized console and daily rotating file logs."""

import sys
from pathlib import Path
from loguru import logger

# Directory where log files will be persisted
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logger(log_level: str = "INFO") -> None:
    """Configures the Loguru logger with both console and rotating file sinks.

    Args:
        log_level: Minimum severity level to log (e.g., DEBUG, INFO, WARNING, ERROR).
    """
    logger.remove()

    # Formatter for colorful, readable console output
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=console_format,
        level=log_level.upper(),
        colorize=True,
    )

    # Formatter for persistent daily rotating file sink
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} - {message}"
    )

    logger.add(
        LOG_DIR / "sweepstakes_{time:YYYY-MM-DD}.log",
        format=file_format,
        level="DEBUG",
        rotation="00:00",  # New file created every midnight
        retention="30 days",
        compression="zip",
        enqueue=True,
        encoding="utf-8",
    )


# Automatically initialize with INFO level by default
setup_logger("INFO")
