"""Slack Bolt runtime wiring."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from slack_vault.config import Settings
from slack_vault.slack_ingest import (
    SlackWebClient,
    build_slack_ingestion_service,
)
from slack_vault.slack_qa import build_slack_qa_service

WorkerSpawner = Callable[[int], None]


def create_slack_web_client(settings: Settings) -> SlackWebClient:
    """Create a Slack SDK WebClient from settings."""

    if settings.slack.bot_token is None:
        raise ValueError("SLACK_BOT_TOKEN is required")
    slack_sdk = cast(Any, import_module("slack_sdk"))
    return cast(SlackWebClient, slack_sdk.WebClient(token=settings.slack.bot_token))


def create_bolt_app(
    settings: Settings,
    *,
    worker_spawner: WorkerSpawner | None = None,
    qa_worker_spawner: WorkerSpawner | None = None,
) -> object:
    """Create a Bolt app with Slack ingestion handlers registered."""

    if settings.slack.bot_token is None:
        raise ValueError("SLACK_BOT_TOKEN is required")
    if worker_spawner is None:
        worker_spawner = spawn_slack_worker_once
    if qa_worker_spawner is None:
        qa_worker_spawner = spawn_slack_qa_worker_once
    slack_bolt = cast(Any, import_module("slack_bolt"))
    app = slack_bolt.App(
        token=settings.slack.bot_token,
        signing_secret=settings.slack.signing_secret,
    )
    service = build_slack_ingestion_service(
        settings,
        slack_client=cast(SlackWebClient, app.client),
    )
    qa_service = build_slack_qa_service(
        settings,
        slack_client=cast(SlackWebClient, app.client),
    )

    def handle_message_events(body: dict[str, object], logger: Any) -> None:
        try:
            _handle_ingestion_event(body, logger, service, worker_spawner)
        except Exception:
            logger.exception("Failed to handle Slack message event")
        try:
            _handle_qa_event(body, logger, qa_service, qa_worker_spawner)
        except Exception:
            logger.exception("Failed to handle Slack Q&A message event")

    def handle_file_shared_events(body: dict[str, object], logger: Any) -> None:
        try:
            _handle_ingestion_event(body, logger, service, worker_spawner)
        except Exception:
            logger.exception("Failed to handle Slack file_shared event")

    app.event("message")(handle_message_events)
    app.event("file_shared")(handle_file_shared_events)

    return cast(object, app)


def spawn_slack_worker_once(count: int = 1) -> None:
    """Start one background Slack worker process per newly queued job."""

    for _index in range(count):
        subprocess.Popen(
            ("make", "slack-worker", "ONCE=1"),
            cwd=Path.cwd(),
        )


def spawn_slack_qa_worker_once(count: int = 1) -> None:
    """Start one background Slack Q&A worker process per newly queued job."""

    for _index in range(count):
        subprocess.Popen(
            ("make", "slack-qa-worker", "ONCE=1"),
            cwd=Path.cwd(),
        )


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


def _handle_ingestion_event(
    body: dict[str, object],
    event_logger: Any,
    service: Any,
    worker_spawner: WorkerSpawner,
) -> None:
    results = service.handle_event_payload(body)
    created_jobs = sum(1 for result in results if result.created)
    if created_jobs == 0:
        return
    event_logger.info("Spawning Slack worker for %s new job(s)", created_jobs)
    worker_spawner(created_jobs)


def _handle_qa_event(
    body: dict[str, object],
    event_logger: Any,
    service: Any,
    worker_spawner: WorkerSpawner,
) -> None:
    results = service.handle_event_payload(body)
    created_jobs = sum(1 for result in results if result.created)
    if created_jobs == 0:
        return
    event_logger.info("Spawning Slack Q&A worker for %s new job(s)", created_jobs)
    worker_spawner(created_jobs)
