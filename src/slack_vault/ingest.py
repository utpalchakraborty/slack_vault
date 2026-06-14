"""Local ingestion orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from slack_vault.archive import (
    ArchivedSourceRef,
    LocalFilesystemArchiveProvider,
    SourceIngestMetadata,
)
from slack_vault.config import ArchiveProviderKind, Settings
from slack_vault.enhancement import (
    EnhancementResult,
    EnhancementStatus,
    EvidenceEnhancer,
)
from slack_vault.evidence_store import (
    EvidenceArtifactWriteResult,
    write_evidence_artifact,
)
from slack_vault.extraction import ExtractionResult, extract_document
from slack_vault.git_vault import VaultCommitter, VaultGitCommitResult
from slack_vault.source_registry import (
    SourceRecordWriteResult,
    generate_source_id,
    write_source_record,
)
from slack_vault.synthesis import (
    KnowledgeSynthesisResult,
    KnowledgeSynthesizer,
    SynthesisStatus,
)

logger = logging.getLogger(__name__)
SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class LocalFileIngestResult:
    """Result of ingesting a local file into archive and source registry."""

    archived_source: ArchivedSourceRef
    extraction_result: ExtractionResult
    enhancement_result: EnhancementResult | None
    evidence_artifact: EvidenceArtifactWriteResult
    synthesis_result: KnowledgeSynthesisResult | None
    source_record: SourceRecordWriteResult
    git_commit: VaultGitCommitResult | None


class IngestProcessingError(RuntimeError):
    """Raised when a requested ingest stage fails before vault commit."""

    def __init__(self, *, source_id: str, stage: str, reason: str) -> None:
        self.source_id = source_id
        self.stage = stage
        self.reason = reason
        super().__init__(f"{stage} failed for {source_id}: {reason}")


def ingest_local_files(
    file_paths: Sequence[Path],
    settings: Settings,
    *,
    uploaded_by: str | None = None,
    overwrite_source_record: bool = False,
    evidence_enhancer: EvidenceEnhancer | None = None,
    knowledge_synthesizer: KnowledgeSynthesizer | None = None,
    vault_committer: VaultCommitter | None = None,
    delay_between_files_seconds: float | None = None,
    sleep: SleepFunction = time.sleep,
    now: datetime | None = None,
) -> tuple[LocalFileIngestResult, ...]:
    """Ingest local files sequentially, waiting between documents when configured."""

    delay_seconds = (
        settings.ingestion.automatic_ingest_delay_seconds
        if delay_between_files_seconds is None
        else delay_between_files_seconds
    )
    if delay_seconds < 0:
        raise ValueError("delay_between_files_seconds must be non-negative")

    results: list[LocalFileIngestResult] = []
    for index, file_path in enumerate(file_paths):
        if index > 0 and delay_seconds > 0:
            logger.info(
                "Waiting between local file ingests delay_seconds=%s next_path=%s",
                delay_seconds,
                file_path,
            )
            sleep(delay_seconds)
        results.append(
            ingest_local_file(
                file_path,
                settings,
                uploaded_by=uploaded_by,
                overwrite_source_record=overwrite_source_record,
                evidence_enhancer=evidence_enhancer,
                knowledge_synthesizer=knowledge_synthesizer,
                vault_committer=vault_committer,
                now=now,
            )
        )
    return tuple(results)


def ingest_local_file(
    file_path: Path,
    settings: Settings,
    *,
    uploaded_by: str | None = None,
    overwrite_source_record: bool = False,
    evidence_enhancer: EvidenceEnhancer | None = None,
    knowledge_synthesizer: KnowledgeSynthesizer | None = None,
    vault_committer: VaultCommitter | None = None,
    now: datetime | None = None,
) -> LocalFileIngestResult:
    """Archive a local file, extract evidence, and write vault records."""

    return ingest_file_path(
        file_path,
        settings,
        metadata=SourceIngestMetadata(
            ingestion_method="local_file",
            original_path=str(file_path.expanduser()),
            uploaded_by=uploaded_by,
        ),
        overwrite_source_record=overwrite_source_record,
        evidence_enhancer=evidence_enhancer,
        knowledge_synthesizer=knowledge_synthesizer,
        vault_committer=vault_committer,
        now=now,
    )


def ingest_file_path(
    file_path: Path,
    settings: Settings,
    *,
    metadata: SourceIngestMetadata,
    overwrite_source_record: bool = False,
    evidence_enhancer: EvidenceEnhancer | None = None,
    knowledge_synthesizer: KnowledgeSynthesizer | None = None,
    vault_committer: VaultCommitter | None = None,
    now: datetime | None = None,
) -> LocalFileIngestResult:
    """Archive a file path with explicit source metadata and write vault records."""

    logger.info(
        "Starting file ingest path=%s method=%s vault_path=%s archive_provider=%s "
        "enhance_requested=%s synthesize_requested=%s git_commit_requested=%s",
        file_path,
        metadata.ingestion_method,
        settings.obsidian_vault_path,
        settings.archive_provider.value,
        evidence_enhancer is not None,
        knowledge_synthesizer is not None,
        vault_committer is not None,
    )
    if settings.archive_provider is not ArchiveProviderKind.LOCAL:
        raise NotImplementedError(
            f"Archive provider is not implemented for local ingest: "
            f"{settings.archive_provider.value}"
        )

    provider = LocalFilesystemArchiveProvider(Path(settings.archive_path))
    archived_source = provider.save_source(file_path, metadata, now=now)
    logger.info(
        "Archived source archive_id=%s content_hash=%s filename=%s size_bytes=%s",
        archived_source.archive_id,
        archived_source.content_hash,
        archived_source.original_filename,
        archived_source.size_bytes,
    )
    extraction_result = extract_document(
        archived_source,
        provider.get_source_path(archived_source),
    )
    logger.info(
        "Extraction finished status=%s extractor=%s evidence_blocks=%s error=%s",
        extraction_result.status.value,
        extraction_result.extractor_name,
        len(extraction_result.evidence),
        extraction_result.error_message,
    )
    enhancement_result = (
        None
        if evidence_enhancer is None
        else evidence_enhancer.enhance(archived_source, extraction_result)
    )
    if enhancement_result is None:
        logger.info("Enhancement not requested")
    else:
        logger.info(
            "Enhancement finished status=%s enhancer=%s enhanced_blocks=%s model=%s "
            "input_tokens=%s output_tokens=%s error=%s",
            enhancement_result.status.value,
            enhancement_result.enhancer_name,
            len(enhancement_result.enhanced_evidence),
            enhancement_result.model,
            enhancement_result.input_tokens,
            enhancement_result.output_tokens,
            enhancement_result.error_message,
        )
    source_id = generate_source_id(archived_source)
    evidence_artifact = write_evidence_artifact(
        Path(settings.archive_path),
        source_id=source_id,
        ref=archived_source,
        extraction_result=extraction_result,
        enhancement_result=enhancement_result,
    )
    _raise_for_failed_enhancement(source_id, enhancement_result)
    synthesis_result = (
        None
        if knowledge_synthesizer is None
        else knowledge_synthesizer.synthesize(
            settings.obsidian_vault_path,
            archived_source,
            source_id,
            extraction_result,
            enhancement_result,
            now=now,
        )
    )
    if synthesis_result is None:
        logger.info("Knowledge synthesis not requested")
    else:
        logger.info(
            "Knowledge synthesis finished status=%s synthesizer=%s note_path=%s "
            "note_created=%s model=%s input_tokens=%s output_tokens=%s error=%s",
            synthesis_result.status.value,
            synthesis_result.synthesizer_name,
            None if synthesis_result.note is None else synthesis_result.note.path,
            None if synthesis_result.note is None else synthesis_result.note.created,
            synthesis_result.model,
            synthesis_result.input_tokens,
            synthesis_result.output_tokens,
            synthesis_result.error_message,
        )
    _raise_for_failed_synthesis(source_id, synthesis_result)
    source_record = write_source_record(
        settings.obsidian_vault_path,
        archived_source,
        extraction_result=extraction_result,
        enhancement_result=enhancement_result,
        evidence_artifact_uri=evidence_artifact.uri,
        evidence_artifact_schema=evidence_artifact.schema,
        overwrite=overwrite_source_record,
    )
    logger.info(
        "Source record write finished source_id=%s path=%s created=%s",
        source_record.source_id,
        source_record.path,
        source_record.created,
    )
    git_commit = (
        None
        if vault_committer is None
        else vault_committer.commit_ingest(
            settings.obsidian_vault_path,
            source_id=source_record.source_id,
            source_filename=archived_source.original_filename,
            source_record_path=source_record.path,
            knowledge_note_paths=()
            if synthesis_result is None or synthesis_result.note is None
            else (synthesis_result.note.path,),
        )
    )
    if git_commit is None:
        logger.info("Vault Git commit not requested")
    else:
        logger.info(
            "Vault Git commit finished committed=%s commit_hash=%s skipped_reason=%s",
            git_commit.committed,
            git_commit.commit_hash,
            git_commit.skipped_reason,
        )
    logger.info(
        "File ingest finished source_id=%s source_record_path=%s",
        source_record.source_id,
        source_record.path,
    )
    return LocalFileIngestResult(
        archived_source=archived_source,
        extraction_result=extraction_result,
        enhancement_result=enhancement_result,
        evidence_artifact=evidence_artifact,
        synthesis_result=synthesis_result,
        source_record=source_record,
        git_commit=git_commit,
    )


def _raise_for_failed_enhancement(
    source_id: str,
    enhancement_result: EnhancementResult | None,
) -> None:
    if (
        enhancement_result is not None
        and enhancement_result.status is EnhancementStatus.FAILED
    ):
        reason = enhancement_result.error_message or "AI enhancement failed."
        logger.error(
            "Requested evidence enhancement failed; stopping ingest before vault "
            "source-record write source_id=%s reason=%s",
            source_id,
            reason,
        )
        raise IngestProcessingError(
            source_id=source_id,
            stage="evidence_enhancement",
            reason=reason,
        )


def _raise_for_failed_synthesis(
    source_id: str,
    synthesis_result: KnowledgeSynthesisResult | None,
) -> None:
    if (
        synthesis_result is not None
        and synthesis_result.status is SynthesisStatus.FAILED
    ):
        reason = synthesis_result.error_message or "Knowledge synthesis failed."
        logger.error(
            "Requested knowledge synthesis failed; stopping ingest before vault "
            "source-record write and Git commit source_id=%s reason=%s",
            source_id,
            reason,
        )
        raise IngestProcessingError(
            source_id=source_id,
            stage="knowledge_synthesis",
            reason=reason,
        )
