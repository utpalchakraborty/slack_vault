"""Application logging setup."""

from __future__ import annotations

import gzip
import logging
import shutil
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from slack_vault.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
PACKAGE_LOGGER_NAME = "slack_vault"


def configure_logging(
    settings: Settings,
    *,
    console: bool = True,
    force: bool = True,
) -> Path:
    """Configure Slack Vault package logging with midnight gzip rotation."""

    log_path = settings.logging.path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if force:
        _remove_handlers(logger)
    elif logger.handlers:
        return log_path

    logger.setLevel(_log_level(settings.logging.level))
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        interval=1,
        backupCount=settings.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.namer = _gzip_namer
    file_handler.rotator = _gzip_rotator
    logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger.info(
        "Logging configured path=%s level=%s rotation=midnight backup_count=%s",
        log_path,
        settings.logging.level,
        settings.logging.backup_count,
    )
    return log_path


def _remove_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _log_level(level: str) -> int:
    resolved = logging.getLevelName(level.upper())
    if not isinstance(resolved, int):
        raise ValueError(f"Unsupported log level: {level}")
    return resolved


def _gzip_namer(default_name: str) -> str:
    if default_name.endswith(".gz"):
        return default_name
    return f"{default_name}.gz"


def _gzip_rotator(source: str, dest: str) -> None:
    source_path = Path(source)
    dest_path = Path(dest)
    with (
        source_path.open("rb") as source_file,
        gzip.open(
            dest_path,
            "wb",
        ) as dest_file,
    ):
        shutil.copyfileobj(source_file, dest_file)
    source_path.unlink()
