from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import ArchiveProviderKind
from slack_vault.source_registry import (
    generate_source_id,
    render_source_record,
    source_record_path,
    write_source_record,
)


def test_generate_source_id_uses_date_and_hash_prefix() -> None:
    ref = _archived_source_ref()

    assert generate_source_id(ref) == "source-2026-06-13-abcdef123456"


def test_render_source_record_includes_archive_and_origin_metadata() -> None:
    ref = _archived_source_ref()
    markdown = render_source_record(ref, "source-2026-06-13-abcdef123456")

    assert 'title: "Example Plan.md"' in markdown
    assert 'source_id: "source-2026-06-13-abcdef123456"' in markdown
    assert 'archive_provider: "local"' in markdown
    assert "size_bytes: 12" in markdown
    assert "# Example Plan.md" in markdown
    assert "- Uploaded by: local-user" in markdown
    assert "Extraction has not run yet." in markdown


def test_write_source_record_is_idempotent_without_overwrite(tmp_path: Path) -> None:
    ref = _archived_source_ref()

    first = write_source_record(tmp_path, ref)
    first.path.write_text("human edit", encoding="utf-8")
    second = write_source_record(tmp_path, ref)

    assert first.created is True
    assert second.created is False
    assert second.path == source_record_path(tmp_path, first.source_id)
    assert second.path.read_text(encoding="utf-8") == "human edit"


def test_write_source_record_can_overwrite(tmp_path: Path) -> None:
    ref = _archived_source_ref()

    first = write_source_record(tmp_path, ref)
    first.path.write_text("human edit", encoding="utf-8")
    second = write_source_record(tmp_path, ref, overwrite=True)

    assert second.created is True
    assert "# Example Plan.md" in second.path.read_text(encoding="utf-8")


def _archived_source_ref() -> ArchivedSourceRef:
    return ArchivedSourceRef(
        archive_provider=ArchiveProviderKind.LOCAL,
        archive_id="sources/2026/06/abcdef1234567890",
        uri="/archive/sources/2026/06/abcdef1234567890/original",
        content_hash="abcdef1234567890",
        original_filename="Example Plan.md",
        mime_type="text/markdown",
        size_bytes=12,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
        ingestion_method="local_file",
        original_path="/tmp/Example Plan.md",
        uploaded_by="local-user",
    )
