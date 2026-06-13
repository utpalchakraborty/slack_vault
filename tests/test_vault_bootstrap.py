from __future__ import annotations

from pathlib import Path

from slack_vault.vault_bootstrap import (
    STARTER_FILES,
    VAULT_DIRECTORIES,
    bootstrap_vault,
)


def test_bootstrap_vault_creates_starter_structure(tmp_path: Path) -> None:
    result = bootstrap_vault(tmp_path)

    for directory in VAULT_DIRECTORIES:
        assert (tmp_path / directory).is_dir()

    for relative_path in STARTER_FILES:
        assert (tmp_path / relative_path).is_file()

    assert result.vault_path == tmp_path
    assert tmp_path / "90 System/ingestion-guidelines.md" in result.created_paths
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == ".obsidian/\n.trash/\n"


def test_bootstrap_vault_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "90 System/ingestion-guidelines.md"
    target.parent.mkdir(parents=True)
    target.write_text("human edit", encoding="utf-8")

    bootstrap_vault(tmp_path)

    assert target.read_text(encoding="utf-8") == "human edit"


def test_bootstrap_vault_can_overwrite_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "90 System/ingestion-guidelines.md"
    target.parent.mkdir(parents=True)
    target.write_text("human edit", encoding="utf-8")

    bootstrap_vault(tmp_path, overwrite=True)

    assert "# Ingestion Guidelines" in target.read_text(encoding="utf-8")
