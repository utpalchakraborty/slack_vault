from __future__ import annotations

from pathlib import Path

import pytest

from slack_vault.cli import main


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

    exit_code = main(["ingest-file", str(source), "--uploaded-by", "tester"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Source: source-" in captured.out
    assert "Extraction status: completed" in captured.out
    assert "Extractor: plain_text" in captured.out
    assert "Evidence blocks: 1" in captured.out
    assert "Enhancement status: not_requested" in captured.out
    assert "Created source record: True" in captured.out
    assert len(list(archive_path.glob("sources/*/*/*/original"))) == 1
    record_paths = list((vault_path / "20 Sources/sources").glob("source-*.md"))
    assert len(record_paths) == 1
    record = record_paths[0].read_text(encoding="utf-8")
    assert 'original_filename: "source.txt"' in record
    assert 'uploaded_by: "tester"' in record
    assert 'extraction_status: "completed"' in record
    assert "source evidence" in record
