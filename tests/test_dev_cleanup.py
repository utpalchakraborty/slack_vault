from __future__ import annotations

from pathlib import Path

import pytest

from slack_vault.config import Settings
from slack_vault.dev_cleanup import clean_poc_data


def test_clean_poc_data_removes_generated_vault_files_and_archive(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    archive_path = tmp_path / "archive"
    generated_note = vault_path / "10 Knowledge/generated.md"
    human_note = vault_path / "10 Knowledge/human.md"
    source_record = vault_path / "20 Sources/sources/source-test.md"
    archive_file = archive_path / "sources/2026/06/hash/original"
    generated_note.parent.mkdir(parents=True)
    human_note.parent.mkdir(parents=True, exist_ok=True)
    source_record.parent.mkdir(parents=True)
    archive_file.parent.mkdir(parents=True)
    generated_note.write_text(
        '---\ntitle: "Generated"\ntype: "knowledge_note"\n---\n# Generated\n',
        encoding="utf-8",
    )
    human_note.write_text("# Human note\n", encoding="utf-8")
    source_record.write_text("# Source\n", encoding="utf-8")
    archive_file.write_text("source", encoding="utf-8")
    settings = Settings.from_env(
        {
            "SLACK_VAULT_OBSIDIAN_PATH": str(vault_path),
            "SLACK_VAULT_ARCHIVE_PATH": str(archive_path),
        }
    )

    result = clean_poc_data(settings)

    assert result.removed_archive is True
    assert set(result.removed_vault_paths) == {generated_note, source_record}
    assert not generated_note.exists()
    assert human_note.is_file()
    assert not source_record.exists()
    assert not archive_path.exists()


def test_clean_poc_data_rejects_non_local_archive_provider(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
            "SLACK_VAULT_ARCHIVE_PROVIDER": "gcs",
            "SLACK_VAULT_ARCHIVE_PATH": "gs://example/archive",
        }
    )

    with pytest.raises(ValueError, match="only supports the local archive provider"):
        clean_poc_data(settings)


def test_clean_poc_data_rejects_unsafe_archive_path(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
            "SLACK_VAULT_ARCHIVE_PATH": str(Path.cwd()),
        }
    )

    with pytest.raises(ValueError, match="Refusing to remove unsafe archive path"):
        clean_poc_data(settings)
