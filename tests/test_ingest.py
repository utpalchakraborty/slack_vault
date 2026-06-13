from __future__ import annotations

from pathlib import Path

from slack_vault.config import Settings
from slack_vault.extraction import ExtractionStatus
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
    assert result.extraction_result.extractor_name == "markdown"
    assert len(result.extraction_result.evidence) == 1
    record = result.source_record.path.read_text(encoding="utf-8")
    assert 'extraction_status: "completed"' in record
    assert 'extractor_name: "markdown"' in record
    assert '- Location: source.md, heading "Overview"' in record
    assert "Source evidence." in record


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
    assert 'extractor_name: "pdf"' in record
    assert "- Status: failed" in record
    assert "- Error:" in record
