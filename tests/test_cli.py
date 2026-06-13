from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slack_vault.cli import main
from slack_vault.config import Settings
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


def _fake_provider_from_settings(settings: Settings) -> object:
    return object()


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
