"""Local ingestion orchestration for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from slack_vault.archive import (
    ArchivedSourceRef,
    LocalFilesystemArchiveProvider,
    SourceIngestMetadata,
)
from slack_vault.config import ArchiveProviderKind, Settings
from slack_vault.source_registry import SourceRecordWriteResult, write_source_record


@dataclass(frozen=True)
class LocalFileIngestResult:
    """Result of ingesting a local file into archive and source registry."""

    archived_source: ArchivedSourceRef
    source_record: SourceRecordWriteResult


def ingest_local_file(
    file_path: Path,
    settings: Settings,
    *,
    uploaded_by: str | None = None,
    overwrite_source_record: bool = False,
    now: datetime | None = None,
) -> LocalFileIngestResult:
    """Archive a local file and write its source record."""

    if settings.archive_provider is not ArchiveProviderKind.LOCAL:
        raise NotImplementedError(
            f"Archive provider is not implemented for local ingest: "
            f"{settings.archive_provider.value}"
        )

    provider = LocalFilesystemArchiveProvider(Path(settings.archive_path))
    metadata = SourceIngestMetadata(
        ingestion_method="local_file",
        original_path=str(file_path.expanduser()),
        uploaded_by=uploaded_by,
    )
    archived_source = provider.save_source(file_path, metadata, now=now)
    source_record = write_source_record(
        settings.obsidian_vault_path,
        archived_source,
        overwrite=overwrite_source_record,
    )
    return LocalFileIngestResult(
        archived_source=archived_source,
        source_record=source_record,
    )
