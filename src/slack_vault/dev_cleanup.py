"""Development cleanup helpers for repeated local POC testing."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from slack_vault.config import ArchiveProviderKind, Settings
from slack_vault.source_registry import SOURCE_RECORDS_DIRECTORY

logger = logging.getLogger(__name__)

KNOWLEDGE_DIRECTORY = Path("10 Knowledge")
KNOWLEDGE_NOTE_TYPE_MARKER = 'type: "knowledge_note"'


@dataclass(frozen=True)
class PocCleanupResult:
    """Summary of generated POC data removed from local storage."""

    vault_path: Path
    archive_path: Path
    removed_vault_paths: tuple[Path, ...]
    removed_archive: bool


def clean_poc_data(settings: Settings) -> PocCleanupResult:
    """Remove generated POC vault artifacts and local archive data."""

    if settings.archive_provider is not ArchiveProviderKind.LOCAL:
        raise ValueError(
            "POC cleanup only supports the local archive provider. "
            f"Configured provider: {settings.archive_provider.value}"
        )

    vault_path = settings.obsidian_vault_path.expanduser()
    archive_path = _safe_archive_path(settings.archive_path)
    removed_vault_paths = _remove_generated_vault_paths(vault_path)
    removed_archive = _remove_archive_path(archive_path)
    logger.info(
        "POC cleanup finished vault_path=%s archive_path=%s "
        "removed_vault_paths=%s removed_archive=%s",
        vault_path,
        archive_path,
        len(removed_vault_paths),
        removed_archive,
    )
    return PocCleanupResult(
        vault_path=vault_path,
        archive_path=archive_path,
        removed_vault_paths=removed_vault_paths,
        removed_archive=removed_archive,
    )


def _remove_generated_vault_paths(vault_path: Path) -> tuple[Path, ...]:
    candidates = [
        *_generated_knowledge_notes(vault_path),
        *_source_records(vault_path),
    ]
    removed: list[Path] = []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        path.unlink()
        removed.append(path)
        logger.info("Removed generated vault file path=%s", path)
    return tuple(removed)


def _generated_knowledge_notes(vault_path: Path) -> tuple[Path, ...]:
    root = vault_path / KNOWLEDGE_DIRECTORY
    if not root.exists():
        return ()
    return tuple(
        path
        for path in root.rglob("*.md")
        if path.is_file() and _is_generated_knowledge_note(path)
    )


def _source_records(vault_path: Path) -> tuple[Path, ...]:
    root = vault_path / SOURCE_RECORDS_DIRECTORY
    if not root.exists():
        return ()
    return tuple(path for path in root.glob("source-*.md") if path.is_file())


def _is_generated_knowledge_note(path: Path) -> bool:
    try:
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
    except UnicodeDecodeError:
        return False
    return KNOWLEDGE_NOTE_TYPE_MARKER in head


def _remove_archive_path(archive_path: Path) -> bool:
    if not archive_path.exists():
        return False
    shutil.rmtree(archive_path)
    logger.info("Removed local archive path=%s", archive_path)
    return True


def _safe_archive_path(raw_archive_path: str) -> Path:
    path = Path(raw_archive_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    forbidden = {
        Path("/").resolve(),
        Path.cwd().resolve(),
        Path.cwd().resolve().parent,
        Path.home().resolve(),
    }
    if resolved in forbidden:
        raise ValueError(f"Refusing to remove unsafe archive path: {resolved}")
    return resolved
