from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import slack_vault.slack_app as slack_app
from slack_vault.config import Settings


def test_create_slack_web_client_uses_configured_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_app, "import_module", _fake_import_module)
    settings = _settings()

    client = slack_app.create_slack_web_client(settings)

    assert isinstance(client, _FakeWebClient)
    assert client.token == "xoxb-token"


def test_create_bolt_app_registers_handlers_and_logs_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_app, "import_module", _fake_import_module)
    service = _FakeSlackIngestionService()
    monkeypatch.setattr(
        slack_app,
        "build_slack_ingestion_service",
        lambda settings, *, slack_client: service,
    )

    app = slack_app.create_bolt_app(_settings())

    assert isinstance(app, _FakeBoltApp)
    assert sorted(app.handlers) == ["file_shared", "message"]

    logger = _FakeLogger()
    app.handlers["message"]({"event": {"type": "message"}}, logger)
    assert service.payloads == [{"event": {"type": "message"}}]
    assert logger.exceptions == []

    service.fail = True
    app.handlers["file_shared"]({"event": {"type": "file_shared"}}, logger)
    assert logger.exceptions == ["Failed to handle Slack file_shared event"]


def test_run_socket_mode_app_starts_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSocketModeHandler.instances = []
    monkeypatch.setattr(slack_app, "import_module", _fake_import_module)
    monkeypatch.setattr(
        slack_app,
        "build_slack_ingestion_service",
        lambda settings, *, slack_client: _FakeSlackIngestionService(),
    )

    slack_app.run_socket_mode_app(_settings())

    assert len(_FakeSocketModeHandler.instances) == 1
    handler = _FakeSocketModeHandler.instances[0]
    assert handler.app_token == "xapp-token"
    assert handler.started is True


def test_slack_app_requires_tokens() -> None:
    missing_bot = Settings.from_env({"SLACK_APP_TOKEN": "xapp-token"})
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        slack_app.create_slack_web_client(missing_bot)
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        slack_app.create_bolt_app(missing_bot)

    missing_app = Settings.from_env({"SLACK_BOT_TOKEN": "xoxb-token"})
    with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
        slack_app.run_socket_mode_app(missing_app)


def _settings() -> Settings:
    return Settings.from_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-token",
            "SLACK_APP_TOKEN": "xapp-token",
            "SLACK_SIGNING_SECRET": "signing-secret",
        }
    )


def _fake_import_module(name: str) -> object:
    if name == "slack_sdk":
        return SimpleNamespace(WebClient=_FakeWebClient)
    if name == "slack_bolt":
        return SimpleNamespace(App=_FakeBoltApp)
    if name == "slack_bolt.adapter.socket_mode":
        return SimpleNamespace(SocketModeHandler=_FakeSocketModeHandler)
    raise ModuleNotFoundError(name)


class _FakeWebClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def files_info(self, **kwargs: object) -> dict[str, object]:
        return {"ok": True, "file": {"id": "F123"}}

    def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        return {"ok": True, "ts": "1718300001.000100"}


class _FakeBoltApp:
    def __init__(self, token: str, signing_secret: str | None) -> None:
        self.token = token
        self.signing_secret = signing_secret
        self.client = _FakeWebClient(token)
        self.handlers: dict[str, Any] = {}

    def event(self, event_name: str) -> Any:
        def register(handler: Any) -> Any:
            self.handlers[event_name] = handler
            return handler

        return register


class _FakeSocketModeHandler:
    instances: list[_FakeSocketModeHandler] = []

    def __init__(self, app: object, app_token: str) -> None:
        self.app = app
        self.app_token = app_token
        self.started = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True


class _FakeSlackIngestionService:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.fail = False

    def handle_event_payload(self, payload: dict[str, object]) -> object:
        if self.fail:
            raise RuntimeError("boom")
        self.payloads.append(payload)
        return ()


class _FakeLogger:
    def __init__(self) -> None:
        self.exceptions: list[str] = []

    def exception(self, message: str) -> None:
        self.exceptions.append(message)
