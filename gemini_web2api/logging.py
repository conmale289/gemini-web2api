"""Loguru-based logging helpers."""
import sys
from pathlib import Path
from typing import Mapping

from loguru import logger

from .config import CONFIG

CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <7}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | "
    "pid={process} thread={thread.name} | {name}:{function}:{line} - {message}"
)


def configure_logging(config: Mapping = CONFIG) -> None:
    """Configure Loguru sinks from app config."""
    logger.remove()
    if not config.get("log_requests", True):
        logger.disable("gemini_web2api")
        return

    logger.enable("gemini_web2api")
    level = str(config.get("log_level") or "INFO").upper()
    logger.add(
        sys.stderr,
        level=level,
        format=config.get("log_console_format") or CONSOLE_FORMAT,
        colorize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    log_file = config.get("log_file")
    if log_file:
        path = Path(str(log_file)).expanduser()
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(path),
            level=level,
            format=config.get("log_file_format") or FILE_FORMAT,
            rotation=config.get("log_rotation") or "20 MB",
            retention=config.get("log_retention") or "7 days",
            compression=config.get("log_compression") or None,
            serialize=bool(config.get("log_json", False)),
            enqueue=True,
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
        )


def log(msg: str, level: str = "INFO", **context) -> None:
    """Compatibility wrapper for existing log call sites."""
    if not CONFIG.get("log_requests", True):
        return
    bound = logger.bind(**context) if context else logger
    bound.log(level.upper(), msg)
