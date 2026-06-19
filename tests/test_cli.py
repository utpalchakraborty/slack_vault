from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slack_vault.cli import main
from slack_vault.config import Settings
from slack_vault.connections import ConnectionStatus, VaultConnectionResult
from slack_vault.ops_state import IngestionJob, IngestionJobStatus, QAJob, QAJobStatus
from slack_vault.qa import AnswerCitation, AnswerResult
from slack_vault.retrieval import AnswerContext
from slack_vault.slack_setup import SlackSetupCheck, SlackSetupCheckResult
from slack_vault.synthesis import (
    KnowledgeNoteWriteResult,
    KnowledgeSynthesisResult,
    SourceClassification,
)


@pytest.fixture(autouse=True)
def _isolate_cli_log_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_VAULT_LOG_PATH", str(tmp_path / "cli.log"))


def test_show_config_prints_redacted_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = main(["show-config"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "claude-haiku-4-5-20251001" in captured.out
    assert "slack_obsidian" in captured.out


def test_init_vault_uses_configured_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(tmp_path))

    exit_code = main(["init-vault"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Initialized vault" in captured.out
    assert (tmp_path / "90 System/prompt-guidelines.md").is_file()


def test_clean_poc_data_requires_confirmation() -> None:
    with pytest.raises(ValueError, match="requires --yes"):
        main(["clean-poc-data"])


def test_clean_poc_data_removes_generated_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault_path = tmp_path / "vault"
    archive_path = tmp_path / "archive"
    generated_note = vault_path / "10 Knowledge/generated.md"
    source_record = vault_path / "20 Sources/sources/source-test.md"
    archive_file = archive_path / "derived/evidence/source-test/evidence.json"
    generated_note.parent.mkdir(parents=True)
    source_record.parent.mkdir(parents=True)
    archive_file.parent.mkdir(parents=True)
    generated_note.write_text(
        '---\ntype: "knowledge_note"\n---\n',
        encoding="utf-8",
    )
    source_record.write_text("# Source\n", encoding="utf-8")
    archive_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(archive_path))

    exit_code = main(["clean-poc-data", "--yes"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Removed vault files: 2" in captured.out
    assert "Removed archive: True" in captured.out
    assert not generated_note.exists()
    assert not source_record.exists()
    assert not archive_path.exists()


def test_ingest_file_archives_source_and_writes_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source evidence", encoding="utf-8")
    archive_path = tmp_path / "archive"
    vault_path = tmp_path / "vault"
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(archive_path))
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PROVIDER", "local")

    exit_code = main(
        [
            "ingest-file",
            str(source),
            "--uploaded-by",
            "tester",
            "--no-git-commit",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Source: source-" in captured.out
    assert "Evidence artifact:" in captured.out
    assert "Extraction status: completed" in captured.out
    assert "Extractor: plain_text" in captured.out
    assert "Evidence blocks: 1" in captured.out
    assert "Enhancement status: not_requested" in captured.out
    assert "Synthesis status: not_requested" in captured.out
    assert "Vault Git commit: not_requested" in captured.out
    assert "Created source record: True" in captured.out
    assert len(list(archive_path.glob("sources/*/*/*/original"))) == 1
    record_paths = list((vault_path / "20 Sources/sources").glob("source-*.md"))
    assert len(record_paths) == 1
    record = record_paths[0].read_text(encoding="utf-8")
    assert 'original_filename: "source.txt"' in record
    assert 'uploaded_by: "tester"' in record
    assert 'extraction_status: "completed"' in record
    assert "Full extracted evidence is stored outside the Git-backed vault." in record
    assert "source evidence" not in record


def test_ingest_file_can_report_synthesized_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source evidence", encoding="utf-8")
    vault_path = tmp_path / "vault"
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "slack_vault.cli.AnthropicAIProvider.from_settings",
        _fake_provider_from_settings,
    )
    monkeypatch.setattr(
        "slack_vault.cli.AnthropicKnowledgeSynthesizer",
        _FakeCliSynthesizer,
    )

    exit_code = main(["ingest-file", str(source), "--synthesize", "--no-git-commit"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Synthesis status: completed" in captured.out
    assert "Knowledge note:" in captured.out
    assert "Created knowledge note: True" in captured.out
    assert "Vault Git commit: not_requested" in captured.out


def test_ingest_file_can_report_connection_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source evidence", encoding="utf-8")
    vault_path = tmp_path / "vault"
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "slack_vault.cli.AnthropicAIProvider.from_settings",
        _fake_provider_from_settings,
    )
    monkeypatch.setattr(
        "slack_vault.cli.AnthropicKnowledgeSynthesizer",
        _FakeCliSynthesizer,
    )
    monkeypatch.setattr(
        "slack_vault.cli.ClaudeAgentVaultConnector.from_settings",
        lambda settings: _FakeCliConnector(vault_path),
    )

    exit_code = main(
        [
            "ingest-file",
            str(source),
            "--synthesize",
            "--connect",
            "--no-git-commit",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Connection status: completed" in captured.out
    assert "Connected vault paths:" in captured.out
    assert "- " in captured.out
    assert "Connection summary: Connected fake note." in captured.out


def test_ingest_file_reports_failed_synthesis_without_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source evidence", encoding="utf-8")
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "slack_vault.cli.AnthropicAIProvider.from_settings",
        _fake_provider_from_settings,
    )
    monkeypatch.setattr(
        "slack_vault.cli.AnthropicKnowledgeSynthesizer",
        _FakeFailingCliSynthesizer,
    )

    exit_code = main(["ingest-file", str(source), "--synthesize"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Ingest failed during knowledge_synthesis: rate limited" in captured.out
    assert "Vault Git commit: not_created" in captured.out
    assert _git(vault_path, "status", "--short") == ""
    assert not (vault_path / "20 Sources/sources").exists()


def test_ingest_file_commits_vault_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source evidence", encoding="utf-8")
    archive_path = tmp_path / "archive"
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(archive_path))
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))

    exit_code = main(["ingest-file", str(source), "--uploaded-by", "tester"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Vault Git commit: " in captured.out
    assert "Vault Git commit: not_requested" not in captured.out
    assert _git(vault_path, "status", "--short") == ""
    assert _git(vault_path, "rev-list", "--count", "HEAD") == "1"
    assert _git(vault_path, "log", "-1", "--pretty=%s").startswith("Ingest source-")
    assert _git(vault_path, "ls-files").startswith("20 Sources/sources/source-")


def test_validate_vault_diff_reports_safe_markdown_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    (vault_path / "10 Knowledge").mkdir(parents=True)
    (vault_path / "20 Sources/sources").mkdir(parents=True)
    (vault_path / "30 Maps").mkdir(parents=True)
    note = vault_path / "10 Knowledge/imported-note.md"
    note.write_text(
        "\n".join(
            [
                "# Imported Note",
                "",
                "Initial body.",
                "",
                "## Sources",
                "",
                "- source-test",
            ]
        ),
        encoding="utf-8",
    )
    (vault_path / "20 Sources/sources/source-test.md").write_text(
        "---\nsource_id: source-test\n---\n",
        encoding="utf-8",
    )
    _run_git(vault_path, "add", ".")
    _run_git(vault_path, "commit", "-m", "Initialize vault")
    note.write_text(
        "\n".join(
            [
                "# Imported Note",
                "",
                "Connected body.",
                "",
                "## Sources",
                "",
                "- source-test",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))

    exit_code = main(
        [
            "validate-vault-diff",
            "--source-id",
            "source-test",
            "--primary-note",
            "10 Knowledge/imported-note.md",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Vault diff validation: ok" in captured.out
    assert "- 10 Knowledge/imported-note.md" in captured.out


def test_ask_requires_ai_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is required"):
        main(["ask", "What is unknown?"])


def test_ask_answers_from_vault_with_mocked_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault_path = tmp_path / "vault"
    calls: list[tuple[Path, str, int]] = []
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(vault_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def answer_question_from_settings(
        settings: Settings,
        question: str,
        *,
        limit: int,
    ) -> AnswerResult:
        calls.append((settings.obsidian_vault_path, question, limit))
        return _cli_answer_result(question)

    monkeypatch.setattr(
        "slack_vault.cli.answer_question_from_settings",
        answer_question_from_settings,
    )

    exit_code = main(["ask", "What does Project Alpha need?", "--limit", "3"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [(vault_path, "What does Project Alpha need?", 3)]
    assert "Answer:" in captured.out
    assert "Project Alpha needs local-first ingest [1]." in captured.out
    assert "[[10 Knowledge/project-alpha-plan|Project Alpha Plan]]" in captured.out
    assert "[[20 Sources/sources/source-alpha|source-alpha]]" in captured.out


def test_run_slack_invokes_socket_mode(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[Settings] = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.setattr(
        "slack_vault.cli.run_socket_mode_app",
        lambda settings: calls.append(settings),
    )

    exit_code = main(["run-slack"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Starting Slack Vault Socket Mode listener" in captured.out
    assert len(calls) == 1
    assert calls[0].slack.bot_token == "xoxb-token"


def test_check_slack_setup_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "slack_vault.cli.run_slack_setup_check",
        lambda settings: SlackSetupCheckResult(
            checks=(SlackSetupCheck("bot auth.test", True, "team=T123"),)
        ),
    )

    exit_code = main(["check-slack-setup"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Slack Vault setup check" in captured.out
    assert "PASS: bot auth.test - team=T123" in captured.out


def test_check_slack_setup_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "slack_vault.cli.run_slack_setup_check",
        lambda settings: SlackSetupCheckResult(
            checks=(
                SlackSetupCheck("channel conversations.info", False, "missing_scope"),
            )
        ),
    )

    exit_code = main(["check-slack-setup"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "FAIL: channel conversations.info - missing_scope" in captured.out


def test_slack_worker_once_reports_empty_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _FakeCliSlackService(tmp_path, ())
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_VAULT_OPERATIONAL_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        "slack_vault.cli.create_slack_web_client", lambda settings: object()
    )
    monkeypatch.setattr(
        "slack_vault.cli.build_slack_ingestion_service",
        lambda settings, *, slack_client: service,
    )

    exit_code = main(["slack-worker", "--once"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No queued Slack ingestion jobs." in captured.out
    assert service.calls == 1


def test_slack_worker_processes_until_queue_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _FakeCliSlackService(
        tmp_path,
        (
            _cli_job(
                job_id="job-first",
                status=IngestionJobStatus.SUCCEEDED,
                source_id="source-123",
                error_message="warning only",
            ),
            _cli_job(job_id="job-second", status=IngestionJobStatus.FAILED),
        ),
    )
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_VAULT_OPERATIONAL_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        "slack_vault.cli.create_slack_web_client", lambda settings: object()
    )
    monkeypatch.setattr(
        "slack_vault.cli.build_slack_ingestion_service",
        lambda settings, *, slack_client: service,
    )

    exit_code = main(["slack-worker"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Processed Slack ingestion job: job-first" in captured.out
    assert "Source: source-123" in captured.out
    assert "Error: warning only" in captured.out
    assert "Processed Slack ingestion job: job-second" in captured.out
    assert "Operational DB:" in captured.out
    assert service.calls == 3


def test_slack_qa_worker_once_reports_empty_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _FakeCliSlackQAService(tmp_path, ())
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_VAULT_OPERATIONAL_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        "slack_vault.cli.create_slack_web_client", lambda settings: object()
    )
    monkeypatch.setattr(
        "slack_vault.cli.build_slack_qa_service",
        lambda settings, *, slack_client: service,
    )

    exit_code = main(["slack-qa-worker", "--once"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No queued Slack Q&A jobs." in captured.out
    assert service.calls == 1


def test_slack_qa_worker_processes_until_queue_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _FakeCliSlackQAService(
        tmp_path,
        (
            _cli_qa_job(
                job_id="qa-job-first",
                status=QAJobStatus.SUCCEEDED,
                answer_text="Answered",
                error_message="warning only",
            ),
            _cli_qa_job(job_id="qa-job-second", status=QAJobStatus.FAILED),
        ),
    )
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_VAULT_OPERATIONAL_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        "slack_vault.cli.create_slack_web_client", lambda settings: object()
    )
    monkeypatch.setattr(
        "slack_vault.cli.build_slack_qa_service",
        lambda settings, *, slack_client: service,
    )

    exit_code = main(["slack-qa-worker"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Processed Slack Q&A job: qa-job-first" in captured.out
    assert "Answered: yes" in captured.out
    assert "Error: warning only" in captured.out
    assert "Processed Slack Q&A job: qa-job-second" in captured.out
    assert "Operational DB:" in captured.out
    assert service.calls == 3


def _fake_provider_from_settings(settings: Settings) -> object:
    return object()


class _FakeCliSlackState:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path


class _FakeCliSlackService:
    def __init__(self, tmp_path: Path, jobs: tuple[IngestionJob, ...]) -> None:
        self.state = _FakeCliSlackState(tmp_path / "ops.db")
        self.jobs = list(jobs)
        self.calls = 0

    def process_next_job(self) -> IngestionJob | None:
        self.calls += 1
        if not self.jobs:
            return None
        return self.jobs.pop(0)


class _FakeCliSlackQAService:
    def __init__(self, tmp_path: Path, jobs: tuple[QAJob, ...]) -> None:
        self.state = _FakeCliSlackState(tmp_path / "ops.db")
        self.jobs = list(jobs)
        self.calls = 0

    def process_next_job(self) -> QAJob | None:
        self.calls += 1
        if not self.jobs:
            return None
        return self.jobs.pop(0)


def _cli_job(
    *,
    job_id: str,
    status: IngestionJobStatus,
    source_id: str | None = None,
    error_message: str | None = None,
) -> IngestionJob:
    return IngestionJob(
        job_id=job_id,
        status=status,
        slack_event_id="Ev123",
        dedupe_key=f"dedupe-{job_id}",
        enterprise_id="E123",
        team_id="T123",
        context_team_id="TCTX",
        channel_id="C123",
        user_id="W123",
        event_ts="1718300000.000200",
        message_ts="1718300000.000100",
        thread_ts="1718300000.000100",
        file_id="F123",
        initial_comment="Please ingest this.",
        file_name="source.md",
        file_mime_type="text/markdown",
        file_size_bytes=12,
        source_id=source_id,
        source_record_path=None,
        knowledge_note_paths=(),
        git_commit_hash=None,
        slack_result_message_ts=None,
        error_stage=None,
        error_message=error_message,
        created_at="2026-06-14T12:00:00Z",
        started_at="2026-06-14T12:01:00Z",
        finished_at="2026-06-14T12:02:00Z",
    )


def _cli_qa_job(
    *,
    job_id: str,
    status: QAJobStatus,
    answer_text: str | None = None,
    error_message: str | None = None,
) -> QAJob:
    return QAJob(
        job_id=job_id,
        status=status,
        slack_event_id="Ev-qa",
        dedupe_key=f"dedupe-{job_id}",
        enterprise_id="E123",
        team_id="T123",
        context_team_id="TCTX",
        channel_id="D123",
        channel_type="im",
        user_id="W123",
        event_ts="1718300000.000200",
        message_ts="1718300000.000100",
        thread_ts=None,
        question_text="What does Project Alpha need?",
        search_query="Project Alpha",
        answer_text=answer_text,
        citations_json="[]",
        slack_initial_message_ts="1718300001.000100",
        slack_result_message_ts=None,
        error_stage=None,
        error_message=error_message,
        created_at="2026-06-14T12:00:00Z",
        started_at="2026-06-14T12:01:00Z",
        finished_at="2026-06-14T12:02:00Z",
    )


class _FakeCliSynthesizer:
    name = "fake"

    def __init__(self, provider: object) -> None:
        self.provider = provider

    def synthesize(
        self,
        vault_path: Path,
        ref: object,
        source_id: str,
        extraction_result: object,
        enhancement_result: object | None = None,
        *,
        now: object | None = None,
    ) -> KnowledgeSynthesisResult:
        note_path = vault_path / "10 Knowledge/fake.md"
        return KnowledgeSynthesisResult.completed(
            synthesizer_name=self.name,
            source_id=source_id,
            note=KnowledgeNoteWriteResult(
                note_id="knowledge-fake",
                title="Fake",
                path=note_path,
                relative_path=note_path.relative_to(vault_path),
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


class _FakeCliConnector:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def connect(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_record_path: Path,
        primary_note_path: Path | None,
    ) -> VaultConnectionResult:
        del vault_path, source_record_path
        touched_path = self.vault_path / "30 Maps/topic-index.md"
        return VaultConnectionResult(
            status=ConnectionStatus.COMPLETED,
            source_id=source_id,
            primary_note_path=primary_note_path,
            touched_paths=(touched_path,),
            agent_summary="Connected fake note.",
        )


class _FakeFailingCliSynthesizer:
    name = "fake"

    def __init__(self, provider: object) -> None:
        self.provider = provider

    def synthesize(
        self,
        vault_path: Path,
        ref: object,
        source_id: str,
        extraction_result: object,
        enhancement_result: object | None = None,
        *,
        now: object | None = None,
    ) -> KnowledgeSynthesisResult:
        return KnowledgeSynthesisResult.failed(
            synthesizer_name=self.name,
            source_id=source_id,
            error_message="rate limited",
        )


def _cli_answer_result(question: str) -> AnswerResult:
    return AnswerResult(
        question=question,
        answer="Project Alpha needs local-first ingest [1].",
        citations=(
            AnswerCitation(
                citation_id=1,
                note_title="Project Alpha Plan",
                note_path=Path("10 Knowledge/project-alpha-plan.md"),
                source_ids=("source-alpha",),
                source_record_paths=(Path("20 Sources/sources/source-alpha.md"),),
            ),
        ),
        context=AnswerContext(
            question=question,
            search_query="Project Alpha",
            items=(),
        ),
        answerer_name="fake",
    )


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _run_git(path, "init")
    _run_git(path, "config", "user.name", "Slack Vault Test")
    _run_git(path, "config", "user.email", "slack-vault@example.test")


def _git(path: Path, *args: str) -> str:
    return _run_git(path, *args).stdout.strip()


def _run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
    )
