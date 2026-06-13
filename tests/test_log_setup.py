from __future__ import annotations

import gzip
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from slack_vault.config import Settings
from slack_vault.log_setup import configure_logging


def test_configure_logging_writes_and_gzips_rotated_logs(tmp_path: Path) -> None:
    log_path = tmp_path / "logs/slack-vault.log"
    settings = Settings.from_env(
        {
            "SLACK_VAULT_LOG_PATH": str(log_path),
            "SLACK_VAULT_LOG_BACKUP_COUNT": "3",
        }
    )

    configured_path = configure_logging(settings, console=False, force=True)
    logger = logging.getLogger("slack_vault.test")
    logger.info("before rotation")
    _flush_package_handlers()

    handler = _timed_rotating_handler()
    handler.doRollover()
    logger.info("after rotation")
    _flush_package_handlers()

    rotated_logs = sorted(log_path.parent.glob("slack-vault.log.*.gz"))
    assert configured_path == log_path
    assert log_path.is_file()
    assert len(rotated_logs) == 1
    with gzip.open(rotated_logs[0], "rt", encoding="utf-8") as log_file:
        rotated_content = log_file.read()
    assert "before rotation" in rotated_content
    assert "after rotation" in log_path.read_text(encoding="utf-8")


def test_configure_logging_rejects_unknown_level(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_LOG_PATH": str(tmp_path / "app.log"),
            "SLACK_VAULT_LOG_LEVEL": "not-a-level",
        }
    )

    with pytest.raises(ValueError, match="Unsupported log level"):
        configure_logging(settings, console=False, force=True)


def _timed_rotating_handler() -> TimedRotatingFileHandler:
    for handler in logging.getLogger("slack_vault").handlers:
        if isinstance(handler, TimedRotatingFileHandler):
            return handler
    raise AssertionError("TimedRotatingFileHandler was not configured")


def _flush_package_handlers() -> None:
    for handler in logging.getLogger("slack_vault").handlers:
        handler.flush()
