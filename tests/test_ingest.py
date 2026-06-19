from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from slack_vault.archive import ArchivedSourceRef, SourceIngestMetadata
from slack_vault.config import Settings
from slack_vault.connections import ConnectionStatus, VaultConnectionResult
from slack_vault.enhancement import (
    EnhancedEvidenceBlock,
    EnhancementResult,
    EnhancementStatus,
)
from slack_vault.extraction import ExtractionResult, ExtractionStatus
from slack_vault.git_vault import VaultGitCommitResult
from slack_vault.ingest import (
    IngestProcessingError,
    ingest_file_path,
    ingest_local_file,
    ingest_local_files,
)
from slack_vault.synthesis import (
    KnowledgeNoteWriteResult,
    KnowledgeSynthesisResult,
    SourceClassification,
    SynthesisStatus,
)


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
    assert result.synthesis_result is None
    assert result.git_commit is None
    assert result.evidence_artifact.path.is_file()
    assert result.extraction_result.extractor_name == "markdown"
    assert len(result.extraction_result.evidence) == 1
    record = result.source_record.path.read_text(encoding="utf-8")
    assert 'extraction_status: "completed"' in record
    assert 'enhancement_status: "not_requested"' in record
    assert 'extractor_name: "markdown"' in record
    assert "evidence_artifact_uri:" in record
    assert "Full extracted evidence is stored outside the Git-backed vault." in record
    assert '- Location: source.md, heading "Overview"' not in record
    assert "Source evidence." not in record
    artifact = json.loads(result.evidence_artifact.path.read_text(encoding="utf-8"))
    assert artifact["extraction"]["evidence"][0]["text"] == (
        "# Overview\n\nSource evidence."
    )


def test_ingest_file_path_accepts_explicit_slack_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
        }
    )

    result = ingest_file_path(
        source,
        settings,
        metadata=SourceIngestMetadata(
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
            slack_file_permalink="https://example.slack.com/files/W123/F123/example",
            slack_event_id="Ev123",
            slack_initial_comment="Please ingest this.",
        ),
    )

    record = result.source_record.path.read_text(encoding="utf-8")

    assert result.archived_source.ingestion_method == "slack_file"
    assert result.archived_source.slack_enterprise_id == "E123"
    assert 'slack_event_id: "Ev123"' in record
    assert 'slack_initial_comment: "Please ingest this."' in record


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
    assert "Source evidence." not in record
    assert "Enhanced source evidence." not in record
    artifact = json.loads(result.evidence_artifact.path.read_text(encoding="utf-8"))
    assert artifact["extraction"]["evidence"][0]["text"] == (
        "# Overview\n\nSource evidence."
    )
    assert artifact["enhancement"]["enhanced_evidence"][0]["text"] == (
        "Enhanced source evidence."
    )


def test_ingest_local_file_can_run_optional_knowledge_synthesis(
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
    synthesizer = _FakeSynthesizer(tmp_path / "vault")

    result = ingest_local_file(source, settings, knowledge_synthesizer=synthesizer)

    assert result.synthesis_result is not None
    assert result.synthesis_result.status is SynthesisStatus.COMPLETED
    assert result.synthesis_result.note is not None
    assert result.synthesis_result.note.created is True
    assert synthesizer.calls == 1
    assert synthesizer.source_ids == (result.source_record.source_id,)


def test_ingest_local_file_can_commit_generated_vault_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
        }
    )
    committer = _FakeCommitter()

    result = ingest_local_file(source, settings, vault_committer=committer)

    assert result.git_commit is not None
    assert result.git_commit.committed is True
    assert committer.calls == 1
    assert committer.source_ids == (result.source_record.source_id,)
    assert committer.source_record_paths == (result.source_record.path,)
    assert committer.knowledge_note_paths == ((),)
    assert committer.connection_note_paths == ((),)


def test_ingest_local_file_can_run_vault_connector_before_commit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    vault_path = tmp_path / "vault"
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(vault_path),
        }
    )
    synthesizer = _FakeSynthesizer(vault_path)
    connector = _FakeConnector()
    committer = _FakeCommitter()

    result = ingest_local_file(
        source,
        settings,
        knowledge_synthesizer=synthesizer,
        vault_connector=connector,
        vault_committer=committer,
    )

    assert result.connection_result is not None
    assert result.connection_result.status is ConnectionStatus.COMPLETED
    assert connector.calls == 1
    assert connector.source_record_exists is True
    assert result.synthesis_result is not None
    assert result.synthesis_result.note is not None
    note_path = result.synthesis_result.note.path
    assert connector.primary_note_paths == (note_path,)
    assert committer.calls == 1
    assert committer.knowledge_note_paths == ((note_path,),)
    assert committer.connection_note_paths == (
        (vault_path / "30 Maps/topic-index.md",),
    )


def test_ingest_local_file_skips_connector_without_synthesized_note(
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
    connector = _FakeConnector()

    result = ingest_local_file(source, settings, vault_connector=connector)

    assert result.connection_result is not None
    assert result.connection_result.status is ConnectionStatus.SKIPPED
    assert result.connection_result.validation_errors == (
        "Connection requires a synthesized primary knowledge note.",
    )
    assert connector.calls == 0


def test_ingest_local_file_stops_before_commit_when_connection_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    vault_path = tmp_path / "vault"
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(vault_path),
        }
    )
    synthesizer = _FakeSynthesizer(vault_path)
    connector = _FailingConnector()
    committer = _FakeCommitter()

    with pytest.raises(
        IngestProcessingError,
        match="vault_connection failed",
    ) as exc_info:
        ingest_local_file(
            source,
            settings,
            knowledge_synthesizer=synthesizer,
            vault_connector=connector,
            vault_committer=committer,
        )

    assert exc_info.value.stage == "vault_connection"
    assert exc_info.value.reason == "unsafe connection change"
    assert connector.calls == 1
    assert connector.source_record_exists is True
    assert committer.calls == 0
    assert (
        vault_path / "20 Sources/sources" / f"{exc_info.value.source_id}.md"
    ).exists()


def test_ingest_local_file_stops_before_commit_when_synthesis_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    vault_path = tmp_path / "vault"
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(vault_path),
        }
    )
    synthesizer = _FailingSynthesizer()
    committer = _FakeCommitter()

    with pytest.raises(
        IngestProcessingError,
        match="knowledge_synthesis failed",
    ) as exc_info:
        ingest_local_file(
            source,
            settings,
            knowledge_synthesizer=synthesizer,
            vault_committer=committer,
        )

    assert exc_info.value.stage == "knowledge_synthesis"
    assert synthesizer.calls == 1
    assert committer.calls == 0
    assert not (
        vault_path / "20 Sources/sources" / f"{exc_info.value.source_id}.md"
    ).exists()


def test_ingest_local_file_stops_before_synthesis_when_enhancement_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Overview\n\nSource evidence.", encoding="utf-8")
    vault_path = tmp_path / "vault"
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(vault_path),
        }
    )
    enhancer = _FailingEnhancer()
    synthesizer = _FakeSynthesizer(vault_path)
    committer = _FakeCommitter()

    with pytest.raises(
        IngestProcessingError,
        match="evidence_enhancement failed",
    ) as exc_info:
        ingest_local_file(
            source,
            settings,
            evidence_enhancer=enhancer,
            knowledge_synthesizer=synthesizer,
            vault_committer=committer,
        )

    assert exc_info.value.stage == "evidence_enhancement"
    assert enhancer.calls == 1
    assert synthesizer.calls == 0
    assert committer.calls == 0
    assert not (
        vault_path / "20 Sources/sources" / f"{exc_info.value.source_id}.md"
    ).exists()


def test_ingest_local_files_waits_between_documents(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("# First\n\nFirst evidence.", encoding="utf-8")
    second.write_text("# Second\n\nSecond evidence.", encoding="utf-8")
    vault_path = tmp_path / "vault"
    settings = Settings.from_env(
        {
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OBSIDIAN_PATH": str(vault_path),
            "SLACK_VAULT_AUTOMATIC_INGEST_DELAY_SECONDS": "2.5",
        }
    )
    sleeps: list[float] = []

    results = ingest_local_files(
        (first, second),
        settings,
        sleep=sleeps.append,
    )

    assert len(results) == 2
    assert sleeps == [2.5]
    assert len(list((vault_path / "20 Sources/sources").glob("source-*.md"))) == 2


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


class _FailingEnhancer:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def enhance(
        self,
        ref: ArchivedSourceRef,
        extraction_result: ExtractionResult,
    ) -> EnhancementResult:
        self.calls += 1
        return EnhancementResult.failed(
            enhancer_name=self.name,
            error_message="rate limited",
        )


class _FakeSynthesizer:
    name = "fake"

    def __init__(self, vault_path: Path) -> None:
        self.calls = 0
        self.source_ids: tuple[str, ...] = ()
        self.vault_path = vault_path

    def synthesize(
        self,
        vault_path: Path,
        ref: ArchivedSourceRef,
        source_id: str,
        extraction_result: ExtractionResult,
        enhancement_result: EnhancementResult | None = None,
        *,
        now: datetime | None = None,
    ) -> KnowledgeSynthesisResult:
        self.calls += 1
        self.source_ids = (*self.source_ids, source_id)
        note_path = vault_path / "10 Knowledge/fake.md"
        return KnowledgeSynthesisResult.completed(
            synthesizer_name=self.name,
            source_id=source_id,
            note=KnowledgeNoteWriteResult(
                note_id="knowledge-fake",
                title="Fake",
                path=note_path,
                relative_path=note_path.relative_to(self.vault_path),
                created=True,
            ),
            classification=SourceClassification(
                document_type="test",
                topics=("test",),
                taxonomy=("tests",),
                summary="Fake summary.",
            ),
            citations=(),
            matched_note_path=None,
            match_confidence=0,
            uncertainty=None,
            model="fake-model",
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )


class _FailingSynthesizer:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(
        self,
        vault_path: Path,
        ref: ArchivedSourceRef,
        source_id: str,
        extraction_result: ExtractionResult,
        enhancement_result: EnhancementResult | None = None,
        *,
        now: datetime | None = None,
    ) -> KnowledgeSynthesisResult:
        self.calls += 1
        return KnowledgeSynthesisResult.failed(
            synthesizer_name=self.name,
            source_id=source_id,
            error_message="rate limited",
        )


class _FakeConnector:
    def __init__(self) -> None:
        self.calls = 0
        self.source_record_exists = False
        self.primary_note_paths: tuple[Path | None, ...] = ()

    def connect(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_record_path: Path,
        primary_note_path: Path | None,
    ) -> VaultConnectionResult:
        self.calls += 1
        self.source_record_exists = source_record_path.exists()
        self.primary_note_paths = (*self.primary_note_paths, primary_note_path)
        touched_path = vault_path / "30 Maps/topic-index.md"
        touched_path.parent.mkdir(parents=True, exist_ok=True)
        touched_path.write_text("- [[fake|Fake]]\n", encoding="utf-8")
        return VaultConnectionResult(
            status=ConnectionStatus.COMPLETED,
            source_id=source_id,
            primary_note_path=primary_note_path,
            touched_paths=(touched_path,),
            agent_summary="Connected fake note.",
        )


class _FailingConnector:
    def __init__(self) -> None:
        self.calls = 0
        self.source_record_exists = False

    def connect(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_record_path: Path,
        primary_note_path: Path | None,
    ) -> VaultConnectionResult:
        del vault_path, primary_note_path
        self.calls += 1
        self.source_record_exists = source_record_path.exists()
        return VaultConnectionResult(
            status=ConnectionStatus.VALIDATION_FAILED,
            source_id=source_id,
            validation_errors=("unsafe connection change",),
        )


class _FakeCommitter:
    def __init__(self) -> None:
        self.calls = 0
        self.source_ids: tuple[str, ...] = ()
        self.source_record_paths: tuple[Path, ...] = ()
        self.knowledge_note_paths: tuple[tuple[Path, ...], ...] = ()
        self.connection_note_paths: tuple[tuple[Path, ...], ...] = ()

    def ensure_clean_worktree(self, vault_path: Path) -> None:
        return None

    def commit_ingest(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_filename: str,
        source_record_path: Path,
        knowledge_note_paths: tuple[Path, ...] = (),
        connection_note_paths: tuple[Path, ...] = (),
    ) -> VaultGitCommitResult:
        self.calls += 1
        self.source_ids = (*self.source_ids, source_id)
        self.source_record_paths = (*self.source_record_paths, source_record_path)
        self.knowledge_note_paths = (*self.knowledge_note_paths, knowledge_note_paths)
        self.connection_note_paths = (
            *self.connection_note_paths,
            connection_note_paths,
        )
        return VaultGitCommitResult(
            committed=True,
            commit_hash="abc123",
            subject=f"Ingest {source_id}",
            body=f"Source filename: {source_filename}",
            paths=(source_record_path, *knowledge_note_paths, *connection_note_paths),
        )
