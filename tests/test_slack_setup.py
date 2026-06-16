from __future__ import annotations

from typing import Any, cast

from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse

from slack_vault.config import Settings
from slack_vault.slack_setup import (
    render_slack_setup_check,
    run_slack_setup_check,
)


def test_slack_setup_check_passes_with_expected_mocked_clients() -> None:
    result = run_slack_setup_check(
        _settings(),
        bot_client=_FakeSetupBotClient(),
        app_client=_FakeSetupAppClient(),
        config_client=_FakeSetupConfigClient(),
    )

    assert result.ok is True
    rendered = render_slack_setup_check(result)
    assert "PASS: SLACK_VAULT_ENTERPRISE_ID optional - not set" in rendered
    assert "PASS: bot auth.test - team=T123 user=U123" in rendered
    assert "PASS: files.info scope - file_not_found" in rendered
    assert "PASS: app token apps.connections.open - websocket url returned" in rendered
    assert "PASS: app manifest Socket Mode" in rendered
    assert "PASS: app manifest App Home Messages tab" in rendered
    assert "PASS: app manifest App Home Messages writable" in rendered
    assert "PASS: app manifest bot events" in rendered
    assert "PASS: app manifest bot scopes" in rendered


def test_slack_setup_check_reports_missing_channel_scope() -> None:
    result = run_slack_setup_check(
        _settings(),
        bot_client=_FakeSetupBotClient(conversations_info_error="missing_scope"),
        app_client=_FakeSetupAppClient(),
        config_client=_FakeSetupConfigClient(),
    )

    assert result.ok is False
    rendered = render_slack_setup_check(result)
    assert "FAIL: channel conversations.info" in rendered
    assert "needed=channels:read,groups:read,mpim:read,im:read" in rendered
    assert "provided=assistant:write" in rendered


def test_slack_setup_check_reports_missing_app_manifest_events() -> None:
    result = run_slack_setup_check(
        _settings(),
        bot_client=_FakeSetupBotClient(),
        app_client=_FakeSetupAppClient(),
        config_client=_FakeSetupConfigClient(bot_events=["file_shared"]),
    )

    assert result.ok is False
    rendered = render_slack_setup_check(result)
    assert "FAIL: app manifest bot events" in rendered
    assert "missing=message.channels" in rendered
    assert "message.im" in rendered


def test_slack_setup_check_reports_missing_app_home_messages() -> None:
    result = run_slack_setup_check(
        _settings(),
        bot_client=_FakeSetupBotClient(),
        app_client=_FakeSetupAppClient(),
        config_client=_FakeSetupConfigClient(
            app_home={
                "home_tab_enabled": True,
                "messages_tab_enabled": False,
                "messages_tab_read_only_enabled": True,
            },
        ),
    )

    assert result.ok is False
    rendered = render_slack_setup_check(result)
    assert "FAIL: app manifest App Home Messages tab" in rendered
    assert "messages_tab_enabled=False" in rendered
    assert "FAIL: app manifest App Home Messages writable" in rendered
    assert "messages_tab_read_only_enabled=True" in rendered


def test_slack_setup_check_reports_missing_qa_scope() -> None:
    result = run_slack_setup_check(
        _settings(),
        bot_client=_FakeSetupBotClient(),
        app_client=_FakeSetupAppClient(),
        config_client=_FakeSetupConfigClient(
            bot_scopes=[
                "chat:write",
                "files:read",
                "channels:read",
                "channels:history",
            ],
        ),
    )

    assert result.ok is False
    rendered = render_slack_setup_check(result)
    assert "FAIL: app manifest bot scopes" in rendered
    assert "missing=im:history" in rendered


def test_slack_setup_check_reports_missing_app_manifest_credentials() -> None:
    result = run_slack_setup_check(
        Settings.from_env(
            {
                "SLACK_BOT_TOKEN": "xoxb-token",
                "SLACK_APP_TOKEN": "xapp-token",
                "SLACK_SIGNING_SECRET": "secret",
                "SLACK_VAULT_TEAM_ID": "T123",
                "SLACK_VAULT_INGESTION_CHANNEL_ID": "C123",
                "SLACK_VAULT_INGESTION_CHANNEL_NAME": "slack-vault-dev-ingest",
            }
        ),
        bot_client=_FakeSetupBotClient(),
        app_client=_FakeSetupAppClient(),
    )

    assert result.ok is False
    rendered = render_slack_setup_check(result)
    assert "FAIL: SLACK_VAULT_APP_ID configured" in rendered
    assert "FAIL: SLACK_APP_CONFIG_TOKEN configured" in rendered


def test_slack_setup_check_reports_missing_config_without_network_clients() -> None:
    result = run_slack_setup_check(Settings.from_env({}))

    assert result.ok is False
    rendered = render_slack_setup_check(result)
    assert "FAIL: SLACK_BOT_TOKEN configured" in rendered
    assert "FAIL: SLACK_APP_TOKEN configured" in rendered
    assert "FAIL: SLACK_SIGNING_SECRET configured" in rendered
    assert "FAIL: SLACK_VAULT_INGESTION_CHANNEL_ID configured" in rendered


def _settings() -> Settings:
    return Settings.from_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-token",
            "SLACK_APP_TOKEN": "xapp-token",
            "SLACK_VAULT_APP_ID": "A123",
            "SLACK_APP_CONFIG_TOKEN": "xoxe-config-token",
            "SLACK_SIGNING_SECRET": "secret",
            "SLACK_VAULT_TEAM_ID": "T123",
            "SLACK_VAULT_INGESTION_CHANNEL_ID": "C123",
            "SLACK_VAULT_INGESTION_CHANNEL_NAME": "slack-vault-dev-ingest",
            "SLACK_VAULT_INGESTION_CHANNEL_IS_PRIVATE": "false",
        }
    )


class _FakeSetupBotClient:
    def __init__(self, *, conversations_info_error: str | None = None) -> None:
        self.conversations_info_error = conversations_info_error

    def auth_test(self) -> dict[str, object]:
        return {"ok": True, "team_id": "T123", "user_id": "U123"}

    def api_test(self) -> dict[str, object]:
        return {"ok": True}

    def conversations_info(self, **kwargs: object) -> dict[str, object]:
        if self.conversations_info_error is not None:
            raise _slack_error(
                self.conversations_info_error,
                needed="channels:read,groups:read,mpim:read,im:read",
                provided="assistant:write",
            )
        assert kwargs == {"channel": "C123"}
        return {
            "ok": True,
            "channel": {
                "id": "C123",
                "name": "slack-vault-dev-ingest",
                "is_member": True,
            },
        }

    def conversations_history(self, **kwargs: object) -> dict[str, object]:
        assert kwargs == {"channel": "C123", "limit": 1}
        return {"ok": True, "messages": []}

    def files_info(self, **kwargs: object) -> dict[str, object]:
        assert kwargs == {"file": "F0000000000"}
        raise _slack_error("file_not_found")


class _FakeSetupAppClient:
    def apps_connections_open(self, **kwargs: object) -> dict[str, object]:
        assert kwargs == {"app_token": "xapp-token"}
        return {"ok": True, "url": "wss://wss-primary.slack.com/link"}


class _FakeSetupConfigClient:
    def __init__(
        self,
        *,
        bot_events: list[str] | None = None,
        bot_scopes: list[str] | None = None,
        app_home: dict[str, object] | None = None,
    ) -> None:
        self.bot_events = bot_events or [
            "file_shared",
            "message.channels",
            "message.im",
        ]
        self.bot_scopes = bot_scopes or [
            "chat:write",
            "files:read",
            "channels:read",
            "channels:history",
            "im:history",
        ]
        self.app_home = app_home or {
            "home_tab_enabled": True,
            "messages_tab_enabled": True,
            "messages_tab_read_only_enabled": False,
        }

    def apps_manifest_export(self, **kwargs: object) -> dict[str, object]:
        assert kwargs == {"app_id": "A123"}
        return {
            "ok": True,
            "manifest": {
                "display_information": {"name": "Slack Vault Dev"},
                "oauth_config": {
                    "scopes": {
                        "bot": self.bot_scopes,
                    }
                },
                "features": {"app_home": self.app_home},
                "settings": {
                    "event_subscriptions": {"bot_events": self.bot_events},
                    "socket_mode_enabled": True,
                },
            },
        }


def _slack_error(error: str, **data: object) -> SlackApiError:
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/test",
        req_args={},
        data={"ok": False, "error": error, **data},
        headers={},
        status_code=200,
    )
    slack_api_error: Any = SlackApiError
    return cast(SlackApiError, slack_api_error(message=error, response=response))
