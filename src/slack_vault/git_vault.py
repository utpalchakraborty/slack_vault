"""Git integration for the generated Obsidian vault."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class VaultGitError(RuntimeError):
    """Raised when vault Git integration cannot proceed."""


@dataclass(frozen=True)
class VaultGitCommitResult:
    """Result of attempting to commit generated vault paths."""

    committed: bool
    commit_hash: str | None
    subject: str
    body: str
    paths: tuple[Path, ...]
    skipped_reason: str | None = None
    pushed: bool = False
    push_skipped_reason: str | None = None


class VaultCommitter(Protocol):
    """Interface for committing generated vault files."""

    def ensure_clean_worktree(self, vault_path: Path) -> None:
        """Fail if the vault has uncommitted changes."""

    def commit_ingest(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_filename: str,
        source_record_path: Path,
        knowledge_note_paths: tuple[Path, ...] = (),
    ) -> VaultGitCommitResult:
        """Commit the generated files for one ingest."""


@dataclass(frozen=True)
class GitVaultCommitter:
    """Git-backed implementation of vault commit operations."""

    push_after_commit: bool = False

    def ensure_clean_worktree(self, vault_path: Path) -> None:
        """Fail if the vault repository has pending changes."""

        _ensure_git_repository(vault_path)
        status = _git(vault_path, "status", "--porcelain", "--untracked-files=normal")
        if status.stdout.strip():
            raise VaultGitError(
                "Vault has uncommitted changes. Commit or remove those changes "
                "before ingesting with Git commit mode, or rerun with "
                "--no-git-commit for local development.\n\n"
                f"Dirty vault entries:\n{status.stdout.strip()}"
            )

    def commit_ingest(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_filename: str,
        source_record_path: Path,
        knowledge_note_paths: tuple[Path, ...] = (),
    ) -> VaultGitCommitResult:
        """Stage and commit one ingest's generated vault files."""

        worktree = _ensure_git_repository(vault_path)
        relative_paths = tuple(
            _relative_to_worktree(path, worktree)
            for path in (source_record_path, *knowledge_note_paths)
        )
        subject, body = build_ingest_commit_message(
            source_id=source_id,
            source_filename=source_filename,
            source_record_path=relative_paths[0],
            knowledge_note_paths=relative_paths[1:],
        )
        logger.info(
            "Committing vault ingest source_id=%s paths=%s",
            source_id,
            tuple(path.as_posix() for path in relative_paths),
        )
        _git(worktree, "add", "--", *(path.as_posix() for path in relative_paths))
        diff = _git(
            worktree,
            "diff",
            "--cached",
            "--quiet",
            "--",
            *(path.as_posix() for path in relative_paths),
            check=False,
        )
        if diff.returncode == 0:
            logger.info("No vault changes to commit source_id=%s", source_id)
            return VaultGitCommitResult(
                committed=False,
                commit_hash=None,
                subject=subject,
                body=body,
                paths=relative_paths,
                skipped_reason="No staged vault changes.",
                push_skipped_reason="No commit to push.",
            )
        if diff.returncode != 1:
            raise VaultGitError(diff.stderr.strip() or "Unable to inspect staged diff")

        _git(worktree, "commit", "-m", subject, "-m", body)
        commit_hash = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        pushed = False
        push_skipped_reason = None
        if self.push_after_commit:
            logger.info(
                "Pushing vault ingest source_id=%s commit_hash=%s",
                source_id,
                commit_hash,
            )
            _git(worktree, "push")
            pushed = True
        else:
            push_skipped_reason = "Push not requested."
        logger.info(
            "Committed vault ingest source_id=%s commit_hash=%s pushed=%s",
            source_id,
            commit_hash,
            pushed,
        )
        return VaultGitCommitResult(
            committed=True,
            commit_hash=commit_hash,
            subject=subject,
            body=body,
            paths=relative_paths,
            pushed=pushed,
            push_skipped_reason=push_skipped_reason,
        )


def build_ingest_commit_message(
    *,
    source_id: str,
    source_filename: str,
    source_record_path: Path,
    knowledge_note_paths: tuple[Path, ...] = (),
) -> tuple[str, str]:
    """Build a structured vault ingest commit message."""

    subject = f"Ingest {source_id}"
    note_lines = (
        [f"- {path.as_posix()}" for path in knowledge_note_paths]
        if knowledge_note_paths
        else ["- None"]
    )
    body = "\n".join(
        [
            f"Source ID: {source_id}",
            f"Source filename: {source_filename}",
            f"Source record: {source_record_path.as_posix()}",
            "",
            "Knowledge notes:",
            *note_lines,
        ]
    )
    return subject, body


def _ensure_git_repository(vault_path: Path) -> Path:
    result = _git(vault_path, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise VaultGitError(
            f"Configured vault path is not inside a Git repository: {vault_path}"
        )
    return Path(result.stdout.strip()).resolve()


def _relative_to_worktree(path: Path, worktree: Path) -> Path:
    try:
        return path.resolve().relative_to(worktree)
    except ValueError as exc:
        raise VaultGitError(
            f"Generated vault path is outside the Git worktree: {path}"
        ) from exc


def _git(
    cwd: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", "-C", str(cwd), *args),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise VaultGitError(result.stderr.strip() or result.stdout.strip())
    return result
