from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from slack_vault.archive import ArchivedSourceRef, SourceIngestMetadata
from slack_vault.config import ArchiveProviderKind, Settings
from slack_vault.evidence_store import EvidenceArtifactWriteResult
from slack_vault.extraction import ExtractionResult
from slack_vault.git_vault import VaultGitCommitResult
from slack_vault.ingest import IngestProcessingError, LocalFileIngestResult
from slack_vault.ops_state import IngestionJobStatus, SQLiteOperationalState
from slack_vault.slack_files import SlackFileInfo
from slack_vault.slack_ingest import (
    SlackIngestDependencies,
    SlackIngestionService,
    default_slack_ingest_dependencies,
)
from slack_vault.source_registry import SourceRecordWriteResult


def test_slack_ingestion_service_enqueues_once_for_duplicate_payload(
    tmp_path: Path,
) -> None:
    service, client, _ingest = _service(tmp_path)
    payload = _message_payload()

    first = service.handle_event_payload(payload)
    second = service.handle_event_payload(payload)

    assert len(first) == 1
    assert first[0].created is True
    assert len(second) == 1
    assert second[0].created is False
    assert len(service.state.list_jobs()) == 1
    assert [message["text"] for message in client.messages] == [
        "Queued Slack Vault ingestion for `F123`."
    ]


def test_slack_ingestion_service_processes_job_with_slack_metadata(
    tmp_path: Path,
) -> None:
    service, client, ingest = _service(tmp_path)
    service.handle_event_payload(_message_payload())

    job = service.process_next_job()

    assert job is not None
    assert job.status is IngestionJobStatus.SUCCEEDED
    assert job.source_id == "source-2026-06-14-abcdef123456"
    assert job.source_record_path is not None
    assert job.git_commit_hash == "abc123"
    assert ingest.metadata is not None
    assert ingest.metadata.ingestion_method == "slack_file"
    assert ingest.metadata.slack_enterprise_id == "E123"
    assert ingest.metadata.slack_team_id == "T123"
    assert ingest.metadata.slack_context_team_id == "TCTX"
    assert ingest.metadata.slack_channel_id == "C123"
    assert ingest.metadata.slack_channel_name == "slack-vault-dev-ingest"
    assert ingest.metadata.slack_file_id == "F123"
    assert ingest.metadata.slack_event_id == "Ev123"
    assert ingest.metadata.slack_initial_comment == "Please ingest this."
    assert ingest.metadata.uploaded_by == "W123"
    assert client.messages[-1]["text"].startswith("Slack Vault ingestion succeeded.")


def test_slack_ingestion_service_marks_failed_job(tmp_path: Path) -> None:
    service, client, ingest = _service(tmp_path)
    ingest.fail = True
    service.handle_event_payload(_message_payload())

    job = service.process_next_job()

    assert job is not None
    assert job.status is IngestionJobStatus.FAILED
    assert job.error_stage == "knowledge_synthesis"
    assert job.error_message == "rate limited"
    assert "knowledge_synthesis" in str(client.messages[-1]["text"])


def test_slack_ingestion_service_marks_failed_when_bot_token_missing(
    tmp_path: Path,
) -> None:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OPERATIONAL_DB_PATH": str(tmp_path / "state.sqlite3"),
            "SLACK_VAULT_INGESTION_CHANNEL_ID": "C123",
        }
    )
    client = _FakeSlackClient()
    service = SlackIngestionService(
        settings=settings,
        state=SQLiteOperationalState(settings.operational.db_path),
        slack_client=client,
        download_root_path=tmp_path / "downloads",
        fetch_file_info=_fake_fetch_file_info,
        download_file=_fake_download_file,
        ingest_path=_FakeIngestPath(tmp_path / "vault"),
        build_dependencies=_no_dependencies,
    )
    service.handle_event_payload(_message_payload())

    job = service.process_next_job()

    assert job is not None
    assert job.status is IngestionJobStatus.FAILED
    assert job.error_stage == "slack_ingest"
    assert "SLACK_BOT_TOKEN" in str(job.error_message)
    assert "SLACK_BOT_TOKEN" in str(client.messages[-1]["text"])


def test_default_slack_ingest_dependencies_respect_runtime_flags(
    tmp_path: Path,
) -> None:
    disabled = Settings.from_env(
        {
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "disabled-vault"),
            "SLACK_VAULT_SLACK_INGEST_ENHANCE": "false",
            "SLACK_VAULT_SLACK_INGEST_SYNTHESIZE": "false",
            "SLACK_VAULT_SLACK_INGEST_GIT_COMMIT": "false",
        }
    )
    disabled_dependencies = default_slack_ingest_dependencies(disabled)

    assert disabled_dependencies.evidence_enhancer is None
    assert disabled_dependencies.knowledge_synthesizer is None
    assert disabled_dependencies.vault_committer is None

    enabled = Settings.from_env(
        {
            "ANTHROPIC_API_KEY": "test-key",
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "enabled-vault"),
            "SLACK_VAULT_SLACK_INGEST_ENHANCE": "true",
            "SLACK_VAULT_SLACK_INGEST_SYNTHESIZE": "true",
            "SLACK_VAULT_SLACK_INGEST_GIT_COMMIT": "true",
        }
    )
    enabled_dependencies = default_slack_ingest_dependencies(enabled)

    assert enabled_dependencies.evidence_enhancer is not None
    assert enabled_dependencies.knowledge_synthesizer is not None
    assert enabled_dependencies.vault_committer is not None


def test_slack_ingestion_service_requires_ingestion_channel(
    tmp_path: Path,
) -> None:
    settings = Settings.from_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-token",
            "SLACK_VAULT_OPERATIONAL_DB_PATH": str(tmp_path / "state.sqlite3"),
        }
    )
    service = SlackIngestionService(
        settings=settings,
        state=SQLiteOperationalState(settings.operational.db_path),
        slack_client=_FakeSlackClient(),
        download_root_path=tmp_path / "downloads",
    )

    with pytest.raises(ValueError, match="INGESTION_CHANNEL_ID"):
        service.handle_event_payload(_message_payload())


def _service(
    tmp_path: Path,
) -> tuple[SlackIngestionService, _FakeSlackClient, _FakeIngestPath]:
    settings = Settings.from_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-token",
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
            "SLACK_VAULT_ARCHIVE_PATH": str(tmp_path / "archive"),
            "SLACK_VAULT_OPERATIONAL_DB_PATH": str(tmp_path / "state.sqlite3"),
            "SLACK_VAULT_INGESTION_CHANNEL_ID": "C123",
            "SLACK_VAULT_INGESTION_CHANNEL_NAME": "slack-vault-dev-ingest",
            "SLACK_VAULT_SLACK_INGEST_SYNTHESIZE": "false",
            "SLACK_VAULT_SLACK_INGEST_GIT_COMMIT": "false",
        }
    )
    client = _FakeSlackClient()
    ingest = _FakeIngestPath(tmp_path / "vault")
    service = SlackIngestionService(
        settings=settings,
        state=SQLiteOperationalState(settings.operational.db_path),
        slack_client=client,
        download_root_path=tmp_path / "downloads",
        fetch_file_info=_fake_fetch_file_info,
        download_file=_fake_download_file,
        ingest_path=ingest,
        build_dependencies=_no_dependencies,
    )
    return service, client, ingest


def _message_payload() -> dict[str, object]:
    return {
        "type": "event_callback",
        "team_id": "T123",
        "context_team_id": "TCTX",
        "context_enterprise_id": "E123",
        "event_id": "Ev123",
        "event": {
            "type": "message",
            "subtype": "file_share",
            "channel": "C123",
            "user": "W123",
            "text": "Please ingest this.",
            "ts": "1718300000.000100",
            "event_ts": "1718300000.000200",
            "files": [{"id": "F123"}],
        },
    }


def _fake_fetch_file_info(
    client: object,
    *,
    file_id: str,
    team_id: str | None = None,
) -> SlackFileInfo:
    assert file_id == "F123"
    assert team_id == "T123"
    return SlackFileInfo(
        file_id="F123",
        name="Example Plan.md",
        title="Example Plan",
        mimetype="text/markdown",
        filetype="markdown",
        size_bytes=123,
        user_id="W123",
        url_private_download="https://files.slack.com/F123/download",
        permalink="https://example.slack.com/files/W123/F123/example",
    )


def _fake_download_file(
    file_info: SlackFileInfo,
    *,
    bot_token: str,
    target_directory: Path,
) -> Path:
    assert file_info.file_id == "F123"
    assert bot_token == "xoxb-token"
    target_directory.mkdir(parents=True)
    path = target_directory / file_info.name
    path.write_text("# Example\n\nEvidence.", encoding="utf-8")
    return path


def _no_dependencies(settings: Settings) -> SlackIngestDependencies:
    return SlackIngestDependencies(
        evidence_enhancer=None,
        knowledge_synthesizer=None,
        vault_committer=_FakeCommitter(),
    )


class _FakeSlackClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def files_info(self, **kwargs: object) -> dict[str, object]:
        return {"ok": True, "file": {"id": "F123"}}

    def chat_postMessage(self, **kwargs: object) -> dict[str, Any]:
        self.messages.append(dict(kwargs))
        return {"ok": True, "ts": f"171830000{len(self.messages)}.000100"}


class _FakeIngestPath:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self.metadata: SourceIngestMetadata | None = None
        self.fail = False

    def __call__(
        self,
        file_path: Path,
        settings: Settings,
        *,
        metadata: SourceIngestMetadata,
        overwrite_source_record: bool = False,
        evidence_enhancer: object | None = None,
        knowledge_synthesizer: object | None = None,
        vault_committer: object | None = None,
        now: datetime | None = None,
    ) -> LocalFileIngestResult:
        if self.fail:
            raise IngestProcessingError(
                source_id="source-2026-06-14-abcdef123456",
                stage="knowledge_synthesis",
                reason="rate limited",
            )
        self.metadata = metadata
        source_id = "source-2026-06-14-abcdef123456"
        source_record_path = self.vault_path / "20 Sources/sources" / f"{source_id}.md"
        return LocalFileIngestResult(
            archived_source=ArchivedSourceRef(
                archive_provider=ArchiveProviderKind.LOCAL,
                archive_id="sources/2026/06/abcdef1234567890",
                uri=str(file_path),
                content_hash="abcdef1234567890",
                original_filename=file_path.name,
                mime_type="text/markdown",
                size_bytes=12,
                created_at=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
                ingestion_method=metadata.ingestion_method,
            ),
            extraction_result=ExtractionResult.completed(
                extractor_name="markdown",
                evidence=(),
            ),
            enhancement_result=None,
            evidence_artifact=EvidenceArtifactWriteResult(
                source_id=source_id,
                path=Path(".data/archive/derived/evidence/evidence.json"),
                uri=".data/archive/derived/evidence/evidence.json",
                schema="slack_vault.evidence.v1",
            ),
            synthesis_result=None,
            source_record=SourceRecordWriteResult(
                source_id=source_id,
                path=source_record_path,
                created=True,
            ),
            git_commit=VaultGitCommitResult(
                committed=True,
                commit_hash="abc123",
                subject=f"Ingest {source_id}",
                body="",
                paths=(source_record_path,),
            ),
        )


class _FakeCommitter:
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
    ) -> VaultGitCommitResult:
        return VaultGitCommitResult(
            committed=True,
            commit_hash="abc123",
            subject=f"Ingest {source_id}",
            body="",
            paths=(source_record_path,),
        )
