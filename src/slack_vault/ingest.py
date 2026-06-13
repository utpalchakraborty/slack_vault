"""Local ingestion orchestration."""

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
from slack_vault.enhancement import EnhancementResult, EvidenceEnhancer
from slack_vault.extraction import ExtractionResult, extract_document
from slack_vault.source_registry import SourceRecordWriteResult, write_source_record


@dataclass(frozen=True)
class LocalFileIngestResult:
    """Result of ingesting a local file into archive and source registry."""

    archived_source: ArchivedSourceRef
    extraction_result: ExtractionResult
    enhancement_result: EnhancementResult | None
    source_record: SourceRecordWriteResult


def ingest_local_file(
    file_path: Path,
    settings: Settings,
    *,
    uploaded_by: str | None = None,
    overwrite_source_record: bool = False,
    evidence_enhancer: EvidenceEnhancer | None = None,
    now: datetime | None = None,
) -> LocalFileIngestResult:
    """Archive a local file, extract evidence, and write its source record."""

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
    extraction_result = extract_document(
        archived_source,
        provider.get_source_path(archived_source),
    )
    enhancement_result = (
        None
        if evidence_enhancer is None
        else evidence_enhancer.enhance(archived_source, extraction_result)
    )
    source_record = write_source_record(
        settings.obsidian_vault_path,
        archived_source,
        extraction_result=extraction_result,
        enhancement_result=enhancement_result,
        overwrite=overwrite_source_record,
    )
    return LocalFileIngestResult(
        archived_source=archived_source,
        extraction_result=extraction_result,
        enhancement_result=enhancement_result,
        source_record=source_record,
    )
