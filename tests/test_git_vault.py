from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slack_vault.git_vault import (
    GitVaultCommitter,
    VaultGitError,
    build_ingest_commit_message,
)


def test_build_ingest_commit_message_includes_source_and_notes() -> None:
    subject, body = build_ingest_commit_message(
        source_id="source-2026-06-13-abcdef123456",
        source_filename="Example.docx",
        source_record_path=Path("20 Sources/sources/source.md"),
        knowledge_note_paths=(Path("10 Knowledge/example.md"),),
        connection_note_paths=(Path("30 Maps/topic-index.md"),),
    )

    assert subject == "Ingest source-2026-06-13-abcdef123456"
    assert "Source ID: source-2026-06-13-abcdef123456" in body
    assert "Source filename: Example.docx" in body
    assert "Source record: 20 Sources/sources/source.md" in body
    assert "- 10 Knowledge/example.md" in body
    assert "Connected vault paths:" in body
    assert "- 30 Maps/topic-index.md" in body


def test_git_vault_committer_creates_commit_for_generated_paths(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    source_record = vault_path / "20 Sources/sources/source-test.md"
    knowledge_note = vault_path / "10 Knowledge/example.md"
    connection_note = vault_path / "30 Maps/topic-index.md"
    source_record.parent.mkdir(parents=True)
    knowledge_note.parent.mkdir(parents=True)
    connection_note.parent.mkdir(parents=True)
    source_record.write_text("# Source\n", encoding="utf-8")
    knowledge_note.write_text("# Knowledge\n", encoding="utf-8")
    connection_note.write_text("# Topic Index\n", encoding="utf-8")

    result = GitVaultCommitter().commit_ingest(
        vault_path,
        source_id="source-test",
        source_filename="Example.docx",
        source_record_path=source_record,
        knowledge_note_paths=(knowledge_note,),
        connection_note_paths=(connection_note,),
    )

    assert result.committed is True
    assert result.commit_hash is not None
    assert result.pushed is False
    assert result.push_skipped_reason == "Push not requested."
    assert result.paths == (
        Path("20 Sources/sources/source-test.md"),
        Path("10 Knowledge/example.md"),
        Path("30 Maps/topic-index.md"),
    )
    assert _git(vault_path, "status", "--short") == ""
    assert _git(vault_path, "log", "-1", "--pretty=%s") == "Ingest source-test"
    commit_body = _git(vault_path, "log", "-1", "--pretty=%b")
    assert "Source filename: Example.docx" in commit_body
    assert "- 10 Knowledge/example.md" in commit_body
    assert "- 30 Maps/topic-index.md" in commit_body
    tracked_files = _git(vault_path, "ls-files").splitlines()
    assert tracked_files == [
        "10 Knowledge/example.md",
        "20 Sources/sources/source-test.md",
        "30 Maps/topic-index.md",
    ]


def test_git_vault_committer_pushes_when_requested(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    remote_path = tmp_path / "remote.git"
    _init_git_repo(vault_path)
    _run_git(vault_path, "branch", "-M", "main")
    _run_git(vault_path, "commit", "--allow-empty", "-m", "Initialize vault")
    _run_git(remote_path.parent, "init", "--bare", str(remote_path))
    _run_git(vault_path, "remote", "add", "origin", str(remote_path))
    _run_git(vault_path, "push", "-u", "origin", "main")
    source_record = vault_path / "20 Sources/sources/source-test.md"
    source_record.parent.mkdir(parents=True)
    source_record.write_text("# Source\n", encoding="utf-8")

    result = GitVaultCommitter(push_after_commit=True).commit_ingest(
        vault_path,
        source_id="source-test",
        source_filename="Example.docx",
        source_record_path=source_record,
    )

    assert result.committed is True
    assert result.pushed is True
    assert result.push_skipped_reason is None
    assert _git_dir(remote_path, "log", "-1", "--pretty=%s") == "Ingest source-test"


def test_git_vault_committer_skips_when_paths_have_no_changes(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    source_record = vault_path / "20 Sources/sources/source-test.md"
    source_record.parent.mkdir(parents=True)
    source_record.write_text("# Source\n", encoding="utf-8")
    committer = GitVaultCommitter()
    first = committer.commit_ingest(
        vault_path,
        source_id="source-test",
        source_filename="Example.docx",
        source_record_path=source_record,
    )

    second = committer.commit_ingest(
        vault_path,
        source_id="source-test",
        source_filename="Example.docx",
        source_record_path=source_record,
    )

    assert first.committed is True
    assert second.committed is False
    assert second.skipped_reason == "No staged vault changes."
    assert second.pushed is False
    assert second.push_skipped_reason == "No commit to push."
    assert _git(vault_path, "rev-list", "--count", "HEAD") == "1"


def test_git_vault_committer_rejects_dirty_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    (vault_path / "dirty.md").write_text("dirty", encoding="utf-8")

    with pytest.raises(VaultGitError, match="Vault has uncommitted changes"):
        GitVaultCommitter().ensure_clean_worktree(vault_path)


def test_git_vault_committer_rejects_non_git_vault(tmp_path: Path) -> None:
    with pytest.raises(VaultGitError, match="not inside a Git repository"):
        GitVaultCommitter().ensure_clean_worktree(tmp_path / "vault")


def test_git_vault_committer_rejects_paths_outside_worktree(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(VaultGitError, match="outside the Git worktree"):
        GitVaultCommitter().commit_ingest(
            vault_path,
            source_id="source-test",
            source_filename="Example.docx",
            source_record_path=outside,
        )


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _run_git(path, "init")
    _run_git(path, "config", "user.name", "Slack Vault Test")
    _run_git(path, "config", "user.email", "slack-vault@example.test")


def _git(path: Path, *args: str) -> str:
    return _run_git(path, *args).stdout.strip()


def _git_dir(path: Path, *args: str) -> str:
    return _run_git_dir(path, *args).stdout.strip()


def _run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def _run_git_dir(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", f"--git-dir={path}", *args),
        check=True,
        capture_output=True,
        text=True,
    )
