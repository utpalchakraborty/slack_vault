"""Slack file metadata, download, and response helpers."""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast


@dataclass(frozen=True)
class SlackFileInfo:
    """Slack-hosted file metadata needed for ingestion."""

    file_id: str
    name: str
    title: str | None
    mimetype: str | None
    filetype: str | None
    size_bytes: int | None
    user_id: str | None
    url_private_download: str
    permalink: str | None


class SlackFilesClient(Protocol):
    """Subset of Slack WebClient used for file metadata."""

    def files_info(self, **kwargs: object) -> Mapping[str, object]:
        """Call Slack files.info."""


class SlackChatClient(Protocol):
    """Subset of Slack WebClient used for posting messages."""

    def chat_postMessage(self, **kwargs: object) -> Mapping[str, object]:
        """Call Slack chat.postMessage."""


DownloadBytes = Callable[[str, str], bytes]


def fetch_slack_file_info(
    client: SlackFilesClient,
    *,
    file_id: str,
    team_id: str | None = None,
) -> SlackFileInfo:
    """Fetch and parse Slack file metadata."""

    kwargs: dict[str, object] = {"file": file_id}
    if team_id is not None:
        kwargs["team_id"] = team_id
    return parse_slack_file_info_response(client.files_info(**kwargs))


def parse_slack_file_info_response(response: Mapping[str, object]) -> SlackFileInfo:
    """Parse a Slack files.info response."""

    if response.get("ok") is False:
        error = response.get("error")
        raise ValueError(f"Slack files.info failed: {error}")

    file = response.get("file")
    if not isinstance(file, Mapping):
        raise ValueError("Slack files.info response did not include a file object")
    file_data = cast(Mapping[str, object], file)

    file_id = _required_string(file_data, "id")
    name = _optional_string(file_data.get("name")) or file_id
    return SlackFileInfo(
        file_id=file_id,
        name=name,
        title=_optional_string(file_data.get("title")),
        mimetype=_optional_string(file_data.get("mimetype")),
        filetype=_optional_string(file_data.get("filetype")),
        size_bytes=_optional_int(file_data.get("size")),
        user_id=_optional_string(file_data.get("user")),
        url_private_download=_required_string(file_data, "url_private_download"),
        permalink=_optional_string(file_data.get("permalink")),
    )


def _urllib_download_bytes(url: str, bot_token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bot_token}"},
    )
    with urllib.request.urlopen(request) as response:
        return cast(bytes, response.read())


def download_slack_file(
    file_info: SlackFileInfo,
    *,
    bot_token: str,
    target_directory: Path,
    download_bytes: DownloadBytes = _urllib_download_bytes,
) -> Path:
    """Download a Slack file with bot-token auth to local temporary storage."""

    target_directory.mkdir(parents=True, exist_ok=True)
    target_path = target_directory / _safe_filename(file_info.name, file_info.file_id)
    target_path.write_bytes(
        download_bytes(file_info.url_private_download, bot_token),
    )
    return target_path


def post_slack_message(
    client: SlackChatClient,
    *,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
) -> str | None:
    """Post a Slack message and return its timestamp when Slack provides one."""

    kwargs: dict[str, object] = {"channel": channel_id, "text": text}
    if thread_ts is not None:
        kwargs["thread_ts"] = thread_ts
    response = client.chat_postMessage(**kwargs)
    if response.get("ok") is False:
        error = response.get("error")
        raise ValueError(f"Slack chat.postMessage failed: {error}")
    return _optional_string(response.get("ts"))


def _safe_filename(name: str, fallback_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not cleaned:
        return fallback_id
    return cleaned[:180]


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = _optional_string(data.get(key))
    if value is None:
        raise ValueError(f"Slack file object must include {key}")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
