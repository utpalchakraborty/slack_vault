from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import ArchiveProviderKind
from slack_vault.enhancement import EnhancedEvidenceBlock, EnhancementResult
from slack_vault.extraction import (
    EvidenceBlock,
    EvidenceLocation,
    EvidenceLocationKind,
    ExtractionResult,
)
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
    assert 'enhancement_status: "not_requested"' in markdown
    assert "AI enhancement has not been requested." in markdown
    assert "Extraction has not run yet." in markdown


def test_render_source_record_includes_slack_enterprise_metadata() -> None:
    ref = ArchivedSourceRef(
        archive_provider=ArchiveProviderKind.LOCAL,
        archive_id="sources/2026/06/abcdef1234567890",
        uri="/archive/sources/2026/06/abcdef1234567890/original",
        content_hash="abcdef1234567890",
        original_filename="Example Plan.md",
        mime_type="text/markdown",
        size_bytes=12,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
        ingestion_method="slack_file",
        original_path="slack://T123/F123",
        uploaded_by="W123",
        slack_workspace_id="T123",
        slack_enterprise_id="E123",
        slack_team_id="T123",
        slack_context_team_id="T456",
        slack_channel_id="C123",
        slack_channel_name="slack-vault-dev-ingest",
        slack_message_ts="1718300000.000100",
        slack_thread_ts="1718300000.000100",
        slack_file_id="F123",
        slack_message_permalink="https://example.slack.com/archives/C123/p1718300000",
        slack_file_permalink="https://example.slack.com/files/W123/F123/example",
        slack_event_id="Ev123",
        slack_initial_comment="Please ingest this.",
    )

    markdown = render_source_record(ref, "source-2026-06-13-abcdef123456")

    assert 'ingestion_method: "slack_file"' in markdown
    assert 'slack_enterprise_id: "E123"' in markdown
    assert 'slack_team_id: "T123"' in markdown
    assert 'slack_context_team_id: "T456"' in markdown
    assert 'slack_channel_name: "slack-vault-dev-ingest"' in markdown
    assert 'slack_thread_ts: "1718300000.000100"' in markdown
    assert 'slack_event_id: "Ev123"' in markdown
    assert 'slack_initial_comment: "Please ingest this."' in markdown
    assert "- Slack Enterprise: E123" in markdown
    assert "- Slack team: T123" in markdown
    assert "- Slack channel name: slack-vault-dev-ingest" in markdown
    assert "- Slack file: https://example.slack.com/files/W123/F123/example" in markdown


def test_render_source_record_includes_extracted_evidence() -> None:
    ref = _archived_source_ref()
    markdown = render_source_record(
        ref,
        "source-2026-06-13-abcdef123456",
        extraction_result=ExtractionResult.completed(
            extractor_name="markdown",
            evidence=(
                EvidenceBlock(
                    sequence=1,
                    text="# Overview\n\nEvidence body.",
                    location=EvidenceLocation(
                        kind=EvidenceLocationKind.HEADING,
                        file_name="Example Plan.md",
                        heading="Overview",
                    ),
                ),
            ),
        ),
        evidence_artifact_uri=".data/archive/derived/evidence/source-test/evidence.json",
        evidence_artifact_schema="slack_vault.evidence.v1",
    )

    assert 'extraction_status: "completed"' in markdown
    assert 'extractor_name: "markdown"' in markdown
    assert "extracted_evidence_count: 1" in markdown
    expected_artifact_frontmatter = (
        "evidence_artifact_uri: "
        '".data/archive/derived/evidence/source-test/evidence.json"'
    )
    assert expected_artifact_frontmatter in markdown
    assert 'evidence_artifact_schema: "slack_vault.evidence.v1"' in markdown
    assert "- Status: completed" in markdown
    assert "- Evidence blocks: 1" in markdown
    assert (
        "- Full evidence artifact: "
        "`.data/archive/derived/evidence/source-test/evidence.json`"
    ) in markdown
    assert "Full extracted evidence is stored outside the Git-backed vault." in markdown
    assert "### Evidence 1" not in markdown
    assert "# Overview\n\nEvidence body." not in markdown


def test_render_source_record_includes_enhanced_evidence() -> None:
    ref = _archived_source_ref()
    location = EvidenceLocation(
        kind=EvidenceLocationKind.HEADING,
        file_name="Example Plan.md",
        heading="Overview",
    )

    markdown = render_source_record(
        ref,
        "source-2026-06-13-abcdef123456",
        extraction_result=ExtractionResult.completed(
            extractor_name="markdown",
            evidence=(
                EvidenceBlock(
                    sequence=1,
                    text="# Overview\n\nEvidence body.",
                    location=location,
                ),
            ),
        ),
        enhancement_result=EnhancementResult.completed(
            enhancer_name="anthropic",
            enhanced_evidence=(
                EnhancedEvidenceBlock(
                    sequence=1,
                    source_sequence=1,
                    text="Clean evidence body.",
                    location=location,
                ),
            ),
            model="claude-test-model",
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=80,
            cache_read_input_tokens=40,
        ),
        evidence_artifact_uri=".data/archive/derived/evidence/source-test/evidence.json",
        evidence_artifact_schema="slack_vault.evidence.v1",
    )

    assert 'enhancement_status: "completed"' in markdown
    assert 'enhancer_name: "anthropic"' in markdown
    assert "enhanced_evidence_count: 1" in markdown
    assert 'enhancement_model: "claude-test-model"' in markdown
    assert "enhancement_input_tokens: 100" in markdown
    assert "enhancement_cache_read_input_tokens: 40" in markdown
    assert "## Extracted Evidence" in markdown
    assert "## Enhanced Evidence" in markdown
    assert (
        "- Full evidence artifact: "
        "`.data/archive/derived/evidence/source-test/evidence.json`"
    ) in markdown
    assert "Full enhanced evidence is stored outside the Git-backed vault." in markdown
    assert "### Enhanced Evidence 1" not in markdown
    assert "Clean evidence body." not in markdown


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
