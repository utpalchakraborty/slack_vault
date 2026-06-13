"""Storage for extracted evidence artifacts outside the Git-backed vault."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef, format_datetime
from slack_vault.enhancement import EnhancementResult
from slack_vault.extraction import EvidenceLocation, ExtractionResult

logger = logging.getLogger(__name__)

EVIDENCE_ARTIFACT_SCHEMA = "slack_vault.evidence.v1"
EVIDENCE_ARTIFACTS_DIRECTORY = Path("derived/evidence")


@dataclass(frozen=True)
class EvidenceArtifactWriteResult:
    """Result of writing a full evidence artifact outside the vault."""

    source_id: str
    path: Path
    uri: str
    schema: str


def write_evidence_artifact(
    archive_root_path: Path,
    *,
    source_id: str,
    ref: ArchivedSourceRef,
    extraction_result: ExtractionResult,
    enhancement_result: EnhancementResult | None = None,
) -> EvidenceArtifactWriteResult:
    """Write full extracted/enhanced evidence to local archive storage."""

    target_path = (
        archive_root_path.expanduser()
        / EVIDENCE_ARTIFACTS_DIRECTORY
        / source_id
        / "evidence.json"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(
            _evidence_artifact_payload(
                source_id=source_id,
                ref=ref,
                extraction_result=extraction_result,
                enhancement_result=enhancement_result,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Evidence artifact written source_id=%s path=%s extraction_blocks=%s "
        "enhancement_blocks=%s",
        source_id,
        target_path,
        len(extraction_result.evidence),
        0 if enhancement_result is None else len(enhancement_result.enhanced_evidence),
    )
    return EvidenceArtifactWriteResult(
        source_id=source_id,
        path=target_path,
        uri=str(target_path),
        schema=EVIDENCE_ARTIFACT_SCHEMA,
    )


def _evidence_artifact_payload(
    *,
    source_id: str,
    ref: ArchivedSourceRef,
    extraction_result: ExtractionResult,
    enhancement_result: EnhancementResult | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": EVIDENCE_ARTIFACT_SCHEMA,
        "source_id": source_id,
        "source": {
            "archive_provider": ref.archive_provider.value,
            "archive_id": ref.archive_id,
            "archive_uri": ref.uri,
            "content_hash": ref.content_hash,
            "original_filename": ref.original_filename,
            "mime_type": ref.mime_type,
            "size_bytes": ref.size_bytes,
            "ingested_at": format_datetime(ref.created_at),
        },
        "extraction": {
            "status": extraction_result.status.value,
            "extractor_name": extraction_result.extractor_name,
            "error_message": extraction_result.error_message,
            "evidence": [
                {
                    "sequence": block.sequence,
                    "text": block.text,
                    "location": _location_payload(block.location),
                }
                for block in extraction_result.evidence
            ],
        },
    }
    if enhancement_result is not None:
        payload["enhancement"] = {
            "status": enhancement_result.status.value,
            "enhancer_name": enhancement_result.enhancer_name,
            "error_message": enhancement_result.error_message,
            "model": enhancement_result.model,
            "input_tokens": enhancement_result.input_tokens,
            "output_tokens": enhancement_result.output_tokens,
            "cache_creation_input_tokens": (
                enhancement_result.cache_creation_input_tokens
            ),
            "cache_read_input_tokens": enhancement_result.cache_read_input_tokens,
            "enhanced_evidence": [
                {
                    "sequence": block.sequence,
                    "source_sequence": block.source_sequence,
                    "text": block.text,
                    "location": _location_payload(block.location),
                }
                for block in enhancement_result.enhanced_evidence
            ],
        }
    return payload


def _location_payload(location: EvidenceLocation) -> dict[str, object]:
    return {
        "kind": location.kind.value,
        "file_name": location.file_name,
        "page_number": location.page_number,
        "heading": location.heading,
        "paragraph_index": location.paragraph_index,
        "table_index": location.table_index,
        "sheet_name": location.sheet_name,
        "cell_range": location.cell_range,
        "label": location.label(),
    }
