from __future__ import annotations

from slack_vault.slack_events import normalize_slack_ingestion_events


def test_normalize_message_event_extracts_file_jobs_and_enterprise_context() -> None:
    payload: dict[str, object] = {
        "type": "event_callback",
        "team_id": "T123",
        "context_team_id": "TCTX",
        "context_enterprise_id": "E123",
        "event_id": "Ev123",
        "authorizations": [
            {
                "enterprise_id": "E123",
                "team_id": "T123",
                "user_id": "W999",
                "is_enterprise_install": True,
            }
        ],
        "event": {
            "type": "message",
            "subtype": "file_share",
            "channel": "C123",
            "user": "W123",
            "text": "Please ingest these.",
            "ts": "1718300000.000100",
            "event_ts": "1718300000.000200",
            "files": [{"id": "F123"}, {"id": "F456"}],
        },
    }

    events = normalize_slack_ingestion_events(
        payload,
        ingestion_channel_id="C123",
    )

    assert len(events) == 2
    assert events[0].event_id == "Ev123"
    assert events[0].event_type == "message"
    assert events[0].enterprise_id == "E123"
    assert events[0].team_id == "T123"
    assert events[0].context_team_id == "TCTX"
    assert events[0].is_enterprise_install is True
    assert events[0].channel_id == "C123"
    assert events[0].user_id == "W123"
    assert events[0].message_ts == "1718300000.000100"
    assert events[0].thread_ts == "1718300000.000100"
    assert events[0].file_id == "F123"
    assert events[0].initial_comment == "Please ingest these."
    assert events[1].file_id == "F456"
    assert events[0].dedupe_key != events[1].dedupe_key


def test_normalize_message_event_ignores_wrong_channel_and_external_shared() -> None:
    payload: dict[str, object] = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev123",
        "is_ext_shared_channel": True,
        "event": {
            "type": "message",
            "channel": "C999",
            "user": "W123",
            "ts": "1718300000.000100",
            "event_ts": "1718300000.000200",
            "files": [{"id": "F123"}],
        },
    }

    assert normalize_slack_ingestion_events(payload, ingestion_channel_id="C123") == ()

    payload["event"] = {
        "type": "message",
        "channel": "C123",
        "user": "W123",
        "ts": "1718300000.000100",
        "event_ts": "1718300000.000200",
        "files": [{"id": "F123"}],
    }

    assert normalize_slack_ingestion_events(payload, ingestion_channel_id="C123") == ()
    assert (
        len(
            normalize_slack_ingestion_events(
                payload,
                ingestion_channel_id="C123",
                allow_external_shared_channels=True,
            )
        )
        == 1
    )


def test_normalize_file_shared_event_uses_file_id_fallback_shape() -> None:
    payload: dict[str, object] = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev-file",
        "event": {
            "type": "file_shared",
            "channel_id": "C123",
            "user_id": "W123",
            "file": {"id": "F123"},
            "event_ts": "1718300000.000300",
        },
    }

    events = normalize_slack_ingestion_events(
        payload,
        ingestion_channel_id="C123",
    )

    assert len(events) == 1
    assert events[0].event_id == "Ev-file"
    assert events[0].event_type == "file_shared"
    assert events[0].file_id == "F123"
    assert events[0].message_ts == "1718300000.000300"


def test_normalize_events_ignores_unsupported_and_bot_payloads() -> None:
    assert normalize_slack_ingestion_events({}, ingestion_channel_id="C123") == ()
    assert (
        normalize_slack_ingestion_events(
            {"event": {"type": "reaction_added"}},
            ingestion_channel_id="C123",
        )
        == ()
    )
    assert (
        normalize_slack_ingestion_events(
            {
                "event": {
                    "type": "message",
                    "subtype": "bot_message",
                    "channel": "C123",
                    "ts": "1718300000.000100",
                    "event_ts": "1718300000.000200",
                    "files": [{"id": "F123"}],
                }
            },
            ingestion_channel_id="C123",
        )
        == ()
    )
    assert (
        normalize_slack_ingestion_events(
            {
                "event": {
                    "type": "message",
                    "channel": "C123",
                    "files": [{"id": "F123"}],
                }
            },
            ingestion_channel_id="C123",
        )
        == ()
    )


def test_normalize_file_shared_event_preserves_enterprise_grid_fallbacks() -> None:
    payload: dict[str, object] = {
        "type": "event_callback",
        "context_team_id": "TCTX",
        "event_id": "Ev-file",
        "authorizations": [
            {
                "enterprise_id": "E123",
                "team_id": "T123",
                "is_enterprise_install": True,
            }
        ],
        "event": {
            "type": "file_shared",
            "channel_id": "C123",
            "user_id": "W123",
            "file_id": "F123",
            "message_ts": "1718300000.000100",
            "thread_ts": "1718300000.000050",
            "event_ts": "1718300000.000300",
        },
    }

    events = normalize_slack_ingestion_events(payload, ingestion_channel_id="C123")

    assert len(events) == 1
    assert events[0].enterprise_id == "E123"
    assert events[0].team_id == "T123"
    assert events[0].context_team_id == "TCTX"
    assert events[0].is_enterprise_install is True
    assert events[0].message_ts == "1718300000.000100"
    assert events[0].thread_ts == "1718300000.000050"


def test_normalize_file_shared_event_rejects_invalid_payloads() -> None:
    payload: dict[str, object] = {
        "is_ext_shared_channel": True,
        "event": {
            "type": "file_shared",
            "channel_id": "C999",
            "file_id": "F123",
            "event_ts": "1718300000.000300",
        },
    }

    assert normalize_slack_ingestion_events(payload, ingestion_channel_id="C123") == ()

    payload["event"] = {
        "type": "file_shared",
        "channel_id": "C123",
        "file_id": "F123",
        "event_ts": "1718300000.000300",
    }
    assert normalize_slack_ingestion_events(payload, ingestion_channel_id="C123") == ()

    payload["is_ext_shared_channel"] = False
    payload["event"] = {
        "type": "file_shared",
        "channel_id": "C123",
        "event_ts": "1718300000.000300",
    }
    assert normalize_slack_ingestion_events(payload, ingestion_channel_id="C123") == ()
