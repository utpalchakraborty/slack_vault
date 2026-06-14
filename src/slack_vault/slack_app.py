"""Slack Bolt runtime wiring."""

from __future__ import annotations

from importlib import import_module
from typing import Any, cast

from slack_vault.config import Settings
from slack_vault.slack_ingest import (
    SlackWebClient,
    build_slack_ingestion_service,
)


def create_slack_web_client(settings: Settings) -> SlackWebClient:
    """Create a Slack SDK WebClient from settings."""

    if settings.slack.bot_token is None:
        raise ValueError("SLACK_BOT_TOKEN is required")
    slack_sdk = cast(Any, import_module("slack_sdk"))
    return cast(SlackWebClient, slack_sdk.WebClient(token=settings.slack.bot_token))


def create_bolt_app(settings: Settings) -> object:
    """Create a Bolt app with Slack ingestion handlers registered."""

    if settings.slack.bot_token is None:
        raise ValueError("SLACK_BOT_TOKEN is required")
    slack_bolt = cast(Any, import_module("slack_bolt"))
    app = slack_bolt.App(
        token=settings.slack.bot_token,
        signing_secret=settings.slack.signing_secret,
    )
    service = build_slack_ingestion_service(
        settings,
        slack_client=cast(SlackWebClient, app.client),
    )

    def handle_message_events(body: dict[str, object], logger: Any) -> None:
        try:
            service.handle_event_payload(body)
        except Exception:
            logger.exception("Failed to handle Slack message event")

    def handle_file_shared_events(body: dict[str, object], logger: Any) -> None:
        try:
            service.handle_event_payload(body)
        except Exception:
            logger.exception("Failed to handle Slack file_shared event")

    app.event("message")(handle_message_events)
    app.event("file_shared")(handle_file_shared_events)

    return cast(object, app)


def run_socket_mode_app(settings: Settings) -> None:
    """Start the Slack Bolt app using Socket Mode."""

    if settings.slack.app_token is None:
        raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")
    slack_socket_mode = cast(Any, import_module("slack_bolt.adapter.socket_mode"))
    handler = slack_socket_mode.SocketModeHandler(
        create_bolt_app(settings),
        settings.slack.app_token,
    )
    handler.start()
