"""Slack event normalization for ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class SlackIngestionEvent:
    """Normalized Slack event that can enqueue one file ingestion job."""

    event_id: str
    event_type: str
    enterprise_id: str | None
    team_id: str | None
    context_team_id: str | None
    is_enterprise_install: bool
    channel_id: str
    user_id: str | None
    event_ts: str
    message_ts: str
    thread_ts: str | None
    file_id: str
    initial_comment: str | None
    is_ext_shared_channel: bool
    raw_payload: Mapping[str, object]

    @property
    def dedupe_key(self) -> str:
        """Return the stable idempotency key for this Slack file ingestion."""

        return "|".join(
            (
                self.enterprise_id or "",
                self.team_id or "",
                self.channel_id,
                self.message_ts,
                self.file_id,
            )
        )


def normalize_slack_ingestion_events(
    payload: Mapping[str, object],
    *,
    ingestion_channel_id: str,
    allow_external_shared_channels: bool = False,
) -> tuple[SlackIngestionEvent, ...]:
    """Normalize a Slack Events API payload into ingestion events.

    Unsupported, irrelevant, or out-of-channel payloads return an empty tuple.
    """

    event = _mapping(payload.get("event"))
    if event is None:
        return ()

    event_type = _string(event.get("type"))
    if event_type == "message":
        return _normalize_message_event(
            payload,
            event,
            ingestion_channel_id=ingestion_channel_id,
            allow_external_shared_channels=allow_external_shared_channels,
        )
    if event_type == "file_shared":
        normalized = _normalize_file_shared_event(
            payload,
            event,
            ingestion_channel_id=ingestion_channel_id,
            allow_external_shared_channels=allow_external_shared_channels,
        )
        return () if normalized is None else (normalized,)
    return ()


def _normalize_message_event(
    payload: Mapping[str, object],
    event: Mapping[str, object],
    *,
    ingestion_channel_id: str,
    allow_external_shared_channels: bool,
) -> tuple[SlackIngestionEvent, ...]:
    channel_id = _string(event.get("channel"))
    if channel_id != ingestion_channel_id:
        return ()
    if _is_external_shared_channel(payload) and not allow_external_shared_channels:
        return ()
    if (
        event.get("bot_id") is not None
        or _string(event.get("subtype")) == "bot_message"
    ):
        return ()

    file_ids = _message_file_ids(event)
    if not file_ids:
        return ()

    event_ts = _string(event.get("event_ts")) or _string(payload.get("event_time"))
    message_ts = _string(event.get("ts")) or event_ts
    if not event_ts or not message_ts:
        return ()

    event_id = _event_id(payload, event_type="message", event_ts=event_ts)
    initial_comment = _blank_to_none(_string(event.get("text")))
    return tuple(
        SlackIngestionEvent(
            event_id=event_id,
            event_type="message",
            enterprise_id=_enterprise_id(payload),
            team_id=_team_id(payload, event),
            context_team_id=_string(payload.get("context_team_id")),
            is_enterprise_install=_is_enterprise_install(payload),
            channel_id=channel_id,
            user_id=_string(event.get("user")),
            event_ts=event_ts,
            message_ts=message_ts,
            thread_ts=_string(event.get("thread_ts")) or message_ts,
            file_id=file_id,
            initial_comment=initial_comment,
            is_ext_shared_channel=_is_external_shared_channel(payload),
            raw_payload=payload,
        )
        for file_id in file_ids
    )


def _normalize_file_shared_event(
    payload: Mapping[str, object],
    event: Mapping[str, object],
    *,
    ingestion_channel_id: str,
    allow_external_shared_channels: bool,
) -> SlackIngestionEvent | None:
    channel_id = _string(event.get("channel_id"))
    if channel_id != ingestion_channel_id:
        return None
    if _is_external_shared_channel(payload) and not allow_external_shared_channels:
        return None

    file_id = _string(event.get("file_id"))
    if file_id is None:
        file = _mapping(event.get("file"))
        file_id = None if file is None else _string(file.get("id"))
    event_ts = _string(event.get("event_ts"))
    if file_id is None or event_ts is None:
        return None

    return SlackIngestionEvent(
        event_id=_event_id(payload, event_type="file_shared", event_ts=event_ts),
        event_type="file_shared",
        enterprise_id=_enterprise_id(payload),
        team_id=_team_id(payload, event),
        context_team_id=_string(payload.get("context_team_id")),
        is_enterprise_install=_is_enterprise_install(payload),
        channel_id=channel_id,
        user_id=_string(event.get("user_id")),
        event_ts=event_ts,
        message_ts=_string(event.get("message_ts")) or event_ts,
        thread_ts=_string(event.get("thread_ts")),
        file_id=file_id,
        initial_comment=None,
        is_ext_shared_channel=_is_external_shared_channel(payload),
        raw_payload=payload,
    )


def _message_file_ids(event: Mapping[str, object]) -> tuple[str, ...]:
    files = event.get("files")
    if not isinstance(files, list):
        return ()

    file_ids: list[str] = []
    for item in files:
        file = _mapping(item)
        if file is None:
            continue
        file_id = _string(file.get("id"))
        if file_id is not None:
            file_ids.append(file_id)
    return tuple(file_ids)


def _event_id(
    payload: Mapping[str, object],
    *,
    event_type: str,
    event_ts: str,
) -> str:
    return _string(payload.get("event_id")) or f"{event_type}:{event_ts}"


def _enterprise_id(payload: Mapping[str, object]) -> str | None:
    return _string(payload.get("context_enterprise_id")) or _authorization_string(
        payload,
        "enterprise_id",
    )


def _team_id(
    payload: Mapping[str, object],
    event: Mapping[str, object],
) -> str | None:
    return (
        _string(payload.get("team_id"))
        or _string(event.get("team"))
        or _string(event.get("source_team"))
        or _authorization_string(payload, "team_id")
    )


def _is_enterprise_install(payload: Mapping[str, object]) -> bool:
    authorizations = payload.get("authorizations")
    if not isinstance(authorizations, list) or not authorizations:
        return False
    authorization = _mapping(authorizations[0])
    if authorization is None:
        return False
    return bool(authorization.get("is_enterprise_install"))


def _authorization_string(
    payload: Mapping[str, object],
    key: str,
) -> str | None:
    authorizations = payload.get("authorizations")
    if not isinstance(authorizations, list) or not authorizations:
        return None
    authorization = _mapping(authorizations[0])
    if authorization is None:
        return None
    return _string(authorization.get(key))


def _is_external_shared_channel(payload: Mapping[str, object]) -> bool:
    return bool(payload.get("is_ext_shared_channel"))


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def _string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _blank_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()
