from __future__ import annotations

import json
from pathlib import Path

from slack_vault.config import (
    ANTHROPIC_HAIKU_45_MAX_INPUT_TOKENS,
    ANTHROPIC_HAIKU_45_MAX_OUTPUT_TOKENS,
    ANTHROPIC_HAIKU_45_MODEL,
    AIProvider,
    ArchiveProvider,
    Settings,
)


def test_settings_default_to_local_slack_obsidian_and_anthropic() -> None:
    settings = Settings.from_env({})

    assert settings.environment == "local"
    assert settings.obsidian_vault_path == Path("/Users/utpalrohan/code/slack_obsidian")
    assert settings.archive_provider is ArchiveProvider.LOCAL
    assert settings.archive_path == ".data/archive"
    assert settings.ai.provider is AIProvider.ANTHROPIC
    assert settings.ai.model == ANTHROPIC_HAIKU_45_MODEL
    assert settings.ai.max_input_tokens == ANTHROPIC_HAIKU_45_MAX_INPUT_TOKENS
    assert settings.ai.max_output_tokens == ANTHROPIC_HAIKU_45_MAX_OUTPUT_TOKENS


def test_settings_read_environment_values() -> None:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ENV": "shared",
            "SLACK_VAULT_OBSIDIAN_PATH": "~/vault",
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
        }
    )

    assert settings.environment == "shared"
    assert settings.obsidian_vault_path == Path("~/vault").expanduser()
    assert settings.archive_provider is ArchiveProvider.GCS
    assert settings.archive_path == "gs://example/archive"
    assert settings.slack.bot_token == "xoxb-abc"
    assert settings.slack.app_token == "xapp-def"
    assert settings.slack.signing_secret == "secret"
    assert settings.slack.ingestion_channel_id == "C123"
    assert settings.ai.anthropic_api_key == "sk-ant-test-value"
    assert settings.ai.model == "custom-model"
    assert settings.ai.max_input_tokens == 123
    assert settings.ai.max_output_tokens == 456


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
