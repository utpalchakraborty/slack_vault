"""Runtime settings for Slack Vault."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

DEFAULT_OBSIDIAN_VAULT_PATH = Path("/Users/utpalrohan/code/slack_obsidian")
DEFAULT_ARCHIVE_PATH = ".data/archive"

ANTHROPIC_HAIKU_45_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_HAIKU_45_MAX_INPUT_TOKENS = 200_000
ANTHROPIC_HAIKU_45_MAX_OUTPUT_TOKENS = 64_000


class AIProvider(StrEnum):
    """Supported AI provider identifiers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class ArchiveProvider(StrEnum):
    """Supported archive provider identifiers."""

    LOCAL = "local"
    GCS = "gcs"


@dataclass(frozen=True)
class SlackSettings:
    """Slack credentials and routing settings."""

    bot_token: str | None
    app_token: str | None
    signing_secret: str | None
    ingestion_channel_id: str | None


@dataclass(frozen=True)
class AISettings:
    """AI provider and model settings."""

    provider: AIProvider
    anthropic_api_key: str | None
    model: str
    max_input_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class Settings:
    """Resolved application settings."""

    environment: str
    obsidian_vault_path: Path
    archive_provider: ArchiveProvider
    archive_path: str
    slack: SlackSettings
    ai: AISettings

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from environment variables."""

        values = os.environ if environ is None else environ
        return cls(
            environment=values.get("SLACK_VAULT_ENV", "local"),
            obsidian_vault_path=_path_value(
                values,
                "SLACK_VAULT_OBSIDIAN_PATH",
                DEFAULT_OBSIDIAN_VAULT_PATH,
            ),
            archive_provider=ArchiveProvider(
                values.get("SLACK_VAULT_ARCHIVE_PROVIDER", ArchiveProvider.LOCAL)
            ),
            archive_path=_string_value(
                values,
                "SLACK_VAULT_ARCHIVE_PATH",
                DEFAULT_ARCHIVE_PATH,
            ),
            slack=SlackSettings(
                bot_token=_blank_to_none(values.get("SLACK_BOT_TOKEN")),
                app_token=_blank_to_none(values.get("SLACK_APP_TOKEN")),
                signing_secret=_blank_to_none(values.get("SLACK_SIGNING_SECRET")),
                ingestion_channel_id=_blank_to_none(
                    values.get("SLACK_VAULT_INGESTION_CHANNEL_ID")
                ),
            ),
            ai=AISettings(
                provider=AIProvider(
                    values.get("SLACK_VAULT_AI_PROVIDER", AIProvider.ANTHROPIC)
                ),
                anthropic_api_key=_blank_to_none(values.get("ANTHROPIC_API_KEY")),
                model=values.get(
                    "SLACK_VAULT_ANTHROPIC_MODEL",
                    ANTHROPIC_HAIKU_45_MODEL,
                ),
                max_input_tokens=_int_value(
                    values,
                    "SLACK_VAULT_AI_MAX_INPUT_TOKENS",
                    ANTHROPIC_HAIKU_45_MAX_INPUT_TOKENS,
                ),
                max_output_tokens=_int_value(
                    values,
                    "SLACK_VAULT_AI_MAX_OUTPUT_TOKENS",
                    ANTHROPIC_HAIKU_45_MAX_OUTPUT_TOKENS,
                ),
            ),
        )

    def as_json(self) -> str:
        """Serialize settings for CLI inspection without exposing secrets."""

        data = {
            "environment": self.environment,
            "obsidian_vault_path": str(self.obsidian_vault_path),
            "archive_provider": self.archive_provider.value,
            "archive_path": self.archive_path,
            "slack": {
                "bot_token": _redact(self.slack.bot_token),
                "app_token": _redact(self.slack.app_token),
                "signing_secret": _redact(self.slack.signing_secret),
                "ingestion_channel_id": self.slack.ingestion_channel_id,
            },
            "ai": {
                "provider": self.ai.provider.value,
                "anthropic_api_key": _redact(self.ai.anthropic_api_key),
                "model": self.ai.model,
                "max_input_tokens": self.ai.max_input_tokens,
                "max_output_tokens": self.ai.max_output_tokens,
            },
        }
        return json.dumps(data, indent=2, sort_keys=True)


def _path_value(values: Mapping[str, str], key: str, default: Path) -> Path:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    return Path(raw_value).expanduser()


def _string_value(values: Mapping[str, str], key: str, default: str) -> str:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value


def _int_value(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    return int(raw_value)


def _blank_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _redact(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
