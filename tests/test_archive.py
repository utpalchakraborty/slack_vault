from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from slack_vault.archive import (
    LocalFilesystemArchiveProvider,
    SourceIngestMetadata,
    detect_mime_type,
    hash_file,
)


def test_hash_file_returns_sha256_digest(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"abc")

    assert (
        hash_file(source)
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_detect_mime_type_uses_binary_fallback(tmp_path: Path) -> None:
    assert detect_mime_type(tmp_path / "note.md") == "text/markdown"
    assert detect_mime_type(tmp_path / "source") == "application/octet-stream"


def test_local_archive_writes_original_and_metadata(tmp_path: Path) -> None:
    source = tmp_path / "plan.txt"
    source.write_text("launch plan", encoding="utf-8")
    provider = LocalFilesystemArchiveProvider(tmp_path / "archive")
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)

    ref = provider.save_source(
        source,
        SourceIngestMetadata(uploaded_by="local-user"),
        now=now,
    )

    archived_source = tmp_path / "archive" / ref.archive_id / "original"
    metadata_path = tmp_path / "archive" / ref.archive_id / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert archived_source.read_text(encoding="utf-8") == "launch plan"
    assert metadata["archive_provider"] == "local"
    assert metadata["content_hash"] == ref.content_hash
    assert metadata["original_filename"] == "plan.txt"
    assert metadata["mime_type"] == "text/plain"
    assert metadata["uploaded_by"] == "local-user"
    assert metadata["created_at"] == "2026-06-13T12:00:00Z"
    assert provider.exists(ref)
    assert provider.get_source_metadata(ref) == ref
    assert provider.get_access_url(ref).startswith("file://")
    assert tuple(provider.list_sources()) == (ref,)


def test_local_archive_is_idempotent_by_content_hash(tmp_path: Path) -> None:
    source = tmp_path / "plan.txt"
    source.write_text("launch plan", encoding="utf-8")
    provider = LocalFilesystemArchiveProvider(tmp_path / "archive")

    first = provider.save_source(
        source,
        SourceIngestMetadata(uploaded_by="first-user"),
        now=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )
    second = provider.save_source(
        source,
        SourceIngestMetadata(uploaded_by="second-user"),
        now=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
    )

    assert second == first
    assert len(tuple(provider.list_sources())) == 1
