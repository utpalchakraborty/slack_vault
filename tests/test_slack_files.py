from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from slack_vault.slack_files import (
    SlackFileInfo,
    download_slack_file,
    fetch_slack_file_info,
    parse_slack_file_info_response,
    post_slack_message,
)


def test_fetch_slack_file_info_passes_team_id_and_parses_response() -> None:
    client = _FakeSlackClient()

    info = fetch_slack_file_info(client, file_id="F123", team_id="T123")

    assert client.files_info_kwargs == {"file": "F123", "team_id": "T123"}
    assert info.file_id == "F123"
    assert info.name == "Example Plan.md"
    assert info.mimetype == "text/markdown"
    assert info.size_bytes == 123
    assert info.url_private_download == "https://files.slack.com/F123/download"
    assert info.permalink == "https://example.slack.com/files/W123/F123/example"


def test_parse_slack_file_info_response_rejects_slack_errors() -> None:
    with pytest.raises(ValueError, match="missing_scope"):
        parse_slack_file_info_response({"ok": False, "error": "missing_scope"})

    with pytest.raises(ValueError, match="file object"):
        parse_slack_file_info_response({"ok": True})

    with pytest.raises(ValueError, match="url_private_download"):
        parse_slack_file_info_response({"ok": True, "file": {"id": "F123"}})


def test_parse_slack_file_info_response_defaults_optional_fields() -> None:
    info = parse_slack_file_info_response(
        {
            "ok": True,
            "file": {
                "id": "F123",
                "name": "",
                "size": False,
                "url_private_download": "https://files.slack.com/F123/download",
            },
        }
    )

    assert info.name == "F123"
    assert info.title is None
    assert info.mimetype is None
    assert info.size_bytes is None


def test_download_slack_file_writes_sanitized_filename(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_download(url: str, token: str) -> bytes:
        calls.append((url, token))
        return b"source bytes"

    path = download_slack_file(
        SlackFileInfo(
            file_id="F123",
            name="../Example Plan?.md",
            title=None,
            mimetype="text/markdown",
            filetype="markdown",
            size_bytes=12,
            user_id="W123",
            url_private_download="https://files.slack.com/F123/download",
            permalink=None,
        ),
        bot_token="xoxb-token",
        target_directory=tmp_path,
        download_bytes=fake_download,
    )

    assert path == tmp_path / "Example_Plan_.md"
    assert path.read_bytes() == b"source bytes"
    assert calls == [("https://files.slack.com/F123/download", "xoxb-token")]


def test_download_slack_file_uses_file_id_for_blank_sanitized_name(
    tmp_path: Path,
) -> None:
    path = download_slack_file(
        SlackFileInfo(
            file_id="F123",
            name="???",
            title=None,
            mimetype=None,
            filetype=None,
            size_bytes=None,
            user_id=None,
            url_private_download="https://files.slack.com/F123/download",
            permalink=None,
        ),
        bot_token="xoxb-token",
        target_directory=tmp_path,
        download_bytes=lambda url, token: b"source bytes",
    )

    assert path == tmp_path / "F123"
    assert path.read_bytes() == b"source bytes"


def test_post_slack_message_returns_ts_and_rejects_errors() -> None:
    client = _FakeSlackClient()

    ts = post_slack_message(
        client,
        channel_id="C123",
        thread_ts="1718300000.000100",
        text="Queued",
    )

    assert ts == "1718300001.000100"
    assert client.chat_kwargs == {
        "channel": "C123",
        "thread_ts": "1718300000.000100",
        "text": "Queued",
    }

    client.fail_chat = True
    with pytest.raises(ValueError, match="not_in_channel"):
        post_slack_message(client, channel_id="C123", text="Queued")


class _FakeSlackClient:
    def __init__(self) -> None:
        self.files_info_kwargs: dict[str, object] = {}
        self.chat_kwargs: dict[str, object] = {}
        self.fail_chat = False

    def files_info(self, **kwargs: object) -> dict[str, object]:
        self.files_info_kwargs = dict(kwargs)
        return {
            "ok": True,
            "file": {
                "id": "F123",
                "name": "Example Plan.md",
                "title": "Example Plan",
                "mimetype": "text/markdown",
                "filetype": "markdown",
                "size": 123,
                "user": "W123",
                "url_private_download": "https://files.slack.com/F123/download",
                "permalink": "https://example.slack.com/files/W123/F123/example",
            },
        }

    def chat_postMessage(self, **kwargs: object) -> dict[str, Any]:
        self.chat_kwargs = dict(kwargs)
        if self.fail_chat:
            return {"ok": False, "error": "not_in_channel"}
        return {"ok": True, "ts": "1718300001.000100"}
