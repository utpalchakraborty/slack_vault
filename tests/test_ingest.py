from __future__ import annotations

from pathlib import Path

from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import Settings
from slack_vault.enhancement import (
    EnhancedEvidenceBlock,
    EnhancementResult,
    EnhancementStatus,
)
from slack_vault.extraction import ExtractionResult, ExtractionStatus
from slack_vault.ingest import ingest_local_file


def test_ingest_local_file_archives_extracts_and_writes_source_record(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
        }
    )

    result = ingest_local_file(source, settings, uploaded_by="tester")

    assert result.extraction_result.status is ExtractionStatus.COMPLETED
    assert result.enhancement_result is None
    assert result.extraction_result.extractor_name == "markdown"
    assert len(result.extraction_result.evidence) == 1
    record = result.source_record.path.read_text(encoding="utf-8")
    assert 'extraction_status: "completed"' in record
    assert 'enhancement_status: "not_requested"' in record
    assert 'extractor_name: "markdown"' in record
    assert '- Location: source.md, heading "Overview"' in record
    assert "Source evidence." in record


def test_ingest_local_file_can_run_optional_evidence_enhancement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
        }
    )
    enhancer = _FakeEnhancer()

    result = ingest_local_file(source, settings, evidence_enhancer=enhancer)

    assert result.enhancement_result is not None
    assert result.enhancement_result.status is EnhancementStatus.COMPLETED
    assert enhancer.calls == 1
    record = result.source_record.path.read_text(encoding="utf-8")
    assert 'extraction_status: "completed"' in record
    assert 'enhancement_status: "completed"' in record
    assert "Source evidence." in record
    assert "Enhanced source evidence." in record


def test_ingest_local_file_records_extraction_failure(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_text("not really a pdf", encoding="utf-8")
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
        }
    )

    result = ingest_local_file(source, settings)

    assert result.extraction_result.status is ExtractionStatus.FAILED
    record = result.source_record.path.read_text(encoding="utf-8")
    assert 'extraction_status: "failed"' in record
    assert 'enhancement_status: "not_requested"' in record
    assert 'extractor_name: "pdf"' in record
    assert "- Status: failed" in record
    assert "- Error:" in record


class _FakeEnhancer:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def enhance(
        self,
        ref: ArchivedSourceRef,
        extraction_result: ExtractionResult,
    ) -> EnhancementResult:
        self.calls += 1
        block = extraction_result.evidence[0]
        return EnhancementResult.completed(
            enhancer_name=self.name,
            enhanced_evidence=(
                EnhancedEvidenceBlock(
                    sequence=1,
                    source_sequence=block.sequence,
                    text="Enhanced source evidence.",
                    location=block.location,
                ),
            ),
            model="fake-model",
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
