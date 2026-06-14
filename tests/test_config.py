from __future__ import annotations

import json
from pathlib import Path

import pytest

from slack_vault.config import (
    ANTHROPIC_HAIKU_45_MAX_INPUT_TOKENS,
    ANTHROPIC_HAIKU_45_MAX_OUTPUT_TOKENS,
    ANTHROPIC_HAIKU_45_MODEL,
    DEFAULT_AI_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_AI_RETRY_INITIAL_DELAY_SECONDS,
    DEFAULT_AI_RETRY_MAX_ATTEMPTS,
    DEFAULT_AI_RETRY_MAX_DELAY_SECONDS,
    DEFAULT_AUTOMATIC_INGEST_DELAY_SECONDS,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_PATH,
    AIProvider,
    ArchiveProviderKind,
    Settings,
)

CONFIG_ENV_KEYS = (
    "SLACK_VAULT_ENV",
    "SLACK_VAULT_OBSIDIAN_PATH",
    "SLACK_VAULT_OBSIDIAN_CLI_VAULT",
    "SLACK_VAULT_ARCHIVE_PROVIDER",
    "SLACK_VAULT_ARCHIVE_PATH",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_VAULT_INGESTION_CHANNEL_ID",
    "SLACK_VAULT_AI_PROVIDER",
    "ANTHROPIC_API_KEY",
    "SLACK_VAULT_ANTHROPIC_MODEL",
    "SLACK_VAULT_AI_MAX_INPUT_TOKENS",
    "SLACK_VAULT_AI_MAX_OUTPUT_TOKENS",
    "SLACK_VAULT_AI_RETRY_MAX_ATTEMPTS",
    "SLACK_VAULT_AI_RETRY_INITIAL_DELAY_SECONDS",
    "SLACK_VAULT_AI_RETRY_MAX_DELAY_SECONDS",
    "SLACK_VAULT_AI_RETRY_BACKOFF_MULTIPLIER",
    "SLACK_VAULT_LOG_PATH",
    "SLACK_VAULT_LOG_LEVEL",
    "SLACK_VAULT_LOG_BACKUP_COUNT",
    "SLACK_VAULT_AUTOMATIC_INGEST_DELAY_SECONDS",
)


def test_settings_default_to_local_slack_obsidian_and_anthropic() -> None:
    settings = Settings.from_env({})

    assert settings.environment == "local"
    assert settings.obsidian_vault_path == Path("/Users/utpalrohan/code/slack_obsidian")
    assert settings.obsidian_cli_vault_name is None
    assert settings.archive_provider is ArchiveProviderKind.LOCAL
    assert settings.archive_path == ".data/archive"
    assert settings.ai.provider is AIProvider.ANTHROPIC
    assert settings.ai.model == ANTHROPIC_HAIKU_45_MODEL
    assert settings.ai.max_input_tokens == ANTHROPIC_HAIKU_45_MAX_INPUT_TOKENS
    assert settings.ai.max_output_tokens == ANTHROPIC_HAIKU_45_MAX_OUTPUT_TOKENS
    assert settings.ai.retry.max_attempts == DEFAULT_AI_RETRY_MAX_ATTEMPTS
    assert (
        settings.ai.retry.initial_delay_seconds
        == DEFAULT_AI_RETRY_INITIAL_DELAY_SECONDS
    )
    assert settings.ai.retry.max_delay_seconds == DEFAULT_AI_RETRY_MAX_DELAY_SECONDS
    assert settings.ai.retry.backoff_multiplier == DEFAULT_AI_RETRY_BACKOFF_MULTIPLIER
    assert settings.logging.path == DEFAULT_LOG_PATH
    assert settings.logging.level == DEFAULT_LOG_LEVEL
    assert settings.logging.backup_count == DEFAULT_LOG_BACKUP_COUNT
    assert (
        settings.ingestion.automatic_ingest_delay_seconds
        == DEFAULT_AUTOMATIC_INGEST_DELAY_SECONDS
    )


def test_settings_read_environment_values() -> None:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ENV": "shared",
            "SLACK_VAULT_OBSIDIAN_PATH": "~/vault",
            "SLACK_VAULT_OBSIDIAN_CLI_VAULT": "Team Vault",
            "SLACK_VAULT_ARCHIVE_PROVIDER": "gcs",
            "SLACK_VAULT_ARCHIVE_PATH": "gs://example/archive",
            "SLACK_BOT_TOKEN": "xoxb-abc",
            "SLACK_APP_TOKEN": "xapp-def",
            "SLACK_SIGNING_SECRET": "secret",
            "SLACK_VAULT_INGESTION_CHANNEL_ID": "C123",
            "SLACK_VAULT_AI_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant-test-value",
            "SLACK_VAULT_ANTHROPIC_MODEL": "custom-model",
            "SLACK_VAULT_AI_MAX_INPUT_TOKENS": "123",
            "SLACK_VAULT_AI_MAX_OUTPUT_TOKENS": "456",
            "SLACK_VAULT_AI_RETRY_MAX_ATTEMPTS": "4",
            "SLACK_VAULT_AI_RETRY_INITIAL_DELAY_SECONDS": "1.5",
            "SLACK_VAULT_AI_RETRY_MAX_DELAY_SECONDS": "9.5",
            "SLACK_VAULT_AI_RETRY_BACKOFF_MULTIPLIER": "1.25",
            "SLACK_VAULT_LOG_PATH": "~/logs/slack-vault.log",
            "SLACK_VAULT_LOG_LEVEL": "debug",
            "SLACK_VAULT_LOG_BACKUP_COUNT": "7",
            "SLACK_VAULT_AUTOMATIC_INGEST_DELAY_SECONDS": "2.5",
        }
    )

    assert settings.environment == "shared"
    assert settings.obsidian_vault_path == Path("~/vault").expanduser()
    assert settings.obsidian_cli_vault_name == "Team Vault"
    assert settings.archive_provider is ArchiveProviderKind.GCS
    assert settings.archive_path == "gs://example/archive"
    assert settings.slack.bot_token == "xoxb-abc"
    assert settings.slack.app_token == "xapp-def"
    assert settings.slack.signing_secret == "secret"
    assert settings.slack.ingestion_channel_id == "C123"
    assert settings.ai.anthropic_api_key == "sk-ant-test-value"
    assert settings.ai.model == "custom-model"
    assert settings.ai.max_input_tokens == 123
    assert settings.ai.max_output_tokens == 456
    assert settings.ai.retry.max_attempts == 4
    assert settings.ai.retry.initial_delay_seconds == 1.5
    assert settings.ai.retry.max_delay_seconds == 9.5
    assert settings.ai.retry.backoff_multiplier == 1.25
    assert settings.logging.path == Path("~/logs/slack-vault.log").expanduser()
    assert settings.logging.level == "DEBUG"
    assert settings.logging.backup_count == 7
    assert settings.ingestion.automatic_ingest_delay_seconds == 2.5


def test_settings_load_dotenv_from_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SLACK_VAULT_ENV=dotenv-local",
                "SLACK_VAULT_OBSIDIAN_PATH=~/dotenv-vault",
                "ANTHROPIC_API_KEY=sk-ant-dotenv-value",
                "SLACK_VAULT_AI_MAX_INPUT_TOKENS=789",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.environment == "dotenv-local"
    assert settings.obsidian_vault_path == Path("~/dotenv-vault").expanduser()
    assert settings.ai.anthropic_api_key == "sk-ant-dotenv-value"
    assert settings.ai.max_input_tokens == 789


def test_settings_environment_values_override_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SLACK_VAULT_ENV=dotenv-local",
                "ANTHROPIC_API_KEY=sk-ant-dotenv-value",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env(
        {
            "SLACK_VAULT_ENV": "explicit-environment",
            "ANTHROPIC_API_KEY": "sk-ant-explicit-value",
        },
        env_file=env_file,
    )

    assert settings.environment == "explicit-environment"
    assert settings.ai.anthropic_api_key == "sk-ant-explicit-value"


def test_settings_json_redacts_secrets() -> None:
    settings = Settings.from_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-secret-token",
            "SLACK_APP_TOKEN": "xapp-secret-token",
            "SLACK_SIGNING_SECRET": "signing-secret",
            "ANTHROPIC_API_KEY": "sk-ant-secret-key",
        }
    )

    payload = json.loads(settings.as_json())

    assert payload["slack"]["bot_token"] == "xoxb...oken"
    assert payload["slack"]["app_token"] == "xapp...oken"
    assert payload["slack"]["signing_secret"] == "sign...cret"
    assert payload["ai"]["anthropic_api_key"] == "sk-a...-key"
    assert payload["ai"]["retry"]["max_attempts"] == 3
    assert payload["logging"]["path"] == ".data/logs/slack-vault.log"
    assert payload["logging"]["level"] == "INFO"
    assert payload["obsidian_cli_vault_name"] is None
    assert payload["ingestion"]["automatic_ingest_delay_seconds"] == 75.0
