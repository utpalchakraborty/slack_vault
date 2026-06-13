from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import ArchiveProviderKind
from slack_vault.enhancement import EnhancedEvidenceBlock, EnhancementResult
from slack_vault.evidence_store import (
    EVIDENCE_ARTIFACT_SCHEMA,
    write_evidence_artifact,
)
from slack_vault.extraction import (
    EvidenceBlock,
    EvidenceLocation,
    EvidenceLocationKind,
    ExtractionResult,
)


def test_write_evidence_artifact_stores_full_extraction_outside_vault(
    tmp_path: Path,
) -> None:
    location = EvidenceLocation(
        kind=EvidenceLocationKind.HEADING,
        file_name="Example Plan.md",
        heading="Overview",
    )
    extraction_result = ExtractionResult.completed(
        extractor_name="markdown",
        evidence=(
            EvidenceBlock(
                sequence=1,
                text="Full extracted evidence body.",
                location=location,
            ),
        ),
    )
    enhancement_result = EnhancementResult.completed(
        enhancer_name="fake",
        enhanced_evidence=(
            EnhancedEvidenceBlock(
                sequence=1,
                source_sequence=1,
                text="Full enhanced evidence body.",
                location=location,
            ),
        ),
        model="fake-model",
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=2,
        cache_read_input_tokens=1,
    )

    result = write_evidence_artifact(
        tmp_path / "archive",
        source_id="source-test",
        ref=_archived_source_ref(),
        extraction_result=extraction_result,
        enhancement_result=enhancement_result,
    )

    payload = json.loads(result.path.read_text(encoding="utf-8"))
    assert result.schema == EVIDENCE_ARTIFACT_SCHEMA
    assert result.uri == str(result.path)
    assert (
        result.path == tmp_path / "archive/derived/evidence/source-test/evidence.json"
    )
    assert payload["schema"] == EVIDENCE_ARTIFACT_SCHEMA
    assert payload["source_id"] == "source-test"
    assert payload["source"]["original_filename"] == "Example Plan.md"
    assert payload["extraction"]["status"] == "completed"
    assert (
        payload["extraction"]["evidence"][0]["text"] == "Full extracted evidence body."
    )
    assert (
        payload["extraction"]["evidence"][0]["location"]["label"]
        == 'Example Plan.md, heading "Overview"'
    )
    assert payload["enhancement"]["status"] == "completed"
    assert (
        payload["enhancement"]["enhanced_evidence"][0]["text"]
        == "Full enhanced evidence body."
    )


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
