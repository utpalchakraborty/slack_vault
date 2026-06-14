"""Archive providers for immutable source files."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from slack_vault.config import ArchiveProviderKind

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SourceIngestMetadata:
    """Metadata known at the time a source enters the system."""

    ingestion_method: str = "local_file"
    original_path: str | None = None
    slack_workspace_id: str | None = None
    slack_enterprise_id: str | None = None
    slack_team_id: str | None = None
    slack_context_team_id: str | None = None
    slack_channel_id: str | None = None
    slack_channel_name: str | None = None
    slack_message_ts: str | None = None
    slack_thread_ts: str | None = None
    slack_file_id: str | None = None
    slack_message_permalink: str | None = None
    slack_file_permalink: str | None = None
    slack_event_id: str | None = None
    slack_initial_comment: str | None = None
    uploaded_by: str | None = None


@dataclass(frozen=True)
class ArchivedSourceRef:
    """Reference to an immutable source file in archive storage."""

    archive_provider: ArchiveProviderKind
    archive_id: str
    uri: str
    content_hash: str
    original_filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    ingestion_method: str
    original_path: str | None = None
    slack_workspace_id: str | None = None
    slack_enterprise_id: str | None = None
    slack_team_id: str | None = None
    slack_context_team_id: str | None = None
    slack_channel_id: str | None = None
    slack_channel_name: str | None = None
    slack_message_ts: str | None = None
    slack_thread_ts: str | None = None
    slack_file_id: str | None = None
    slack_message_permalink: str | None = None
    slack_file_permalink: str | None = None
    slack_event_id: str | None = None
    slack_initial_comment: str | None = None
    uploaded_by: str | None = None

    def to_metadata_dict(self) -> dict[str, object]:
        """Serialize the reference for archive metadata storage."""

        data = asdict(self)
        data["archive_provider"] = self.archive_provider.value
        data["created_at"] = format_datetime(self.created_at)
        return data

    @classmethod
    def from_metadata_dict(cls, data: dict[str, object]) -> ArchivedSourceRef:
        """Deserialize a reference from archive metadata storage."""

        return cls(
            archive_provider=ArchiveProviderKind(str(data["archive_provider"])),
            archive_id=str(data["archive_id"]),
            uri=str(data["uri"]),
            content_hash=str(data["content_hash"]),
            original_filename=str(data["original_filename"]),
            mime_type=str(data["mime_type"]),
            size_bytes=int(str(data["size_bytes"])),
            created_at=parse_datetime(str(data["created_at"])),
            ingestion_method=str(data["ingestion_method"]),
            original_path=_optional_str(data.get("original_path")),
            slack_workspace_id=_optional_str(data.get("slack_workspace_id")),
            slack_enterprise_id=_optional_str(data.get("slack_enterprise_id")),
            slack_team_id=_optional_str(data.get("slack_team_id")),
            slack_context_team_id=_optional_str(data.get("slack_context_team_id")),
            slack_channel_id=_optional_str(data.get("slack_channel_id")),
            slack_channel_name=_optional_str(data.get("slack_channel_name")),
            slack_message_ts=_optional_str(data.get("slack_message_ts")),
            slack_thread_ts=_optional_str(data.get("slack_thread_ts")),
            slack_file_id=_optional_str(data.get("slack_file_id")),
            slack_message_permalink=_optional_str(data.get("slack_message_permalink")),
            slack_file_permalink=_optional_str(data.get("slack_file_permalink")),
            slack_event_id=_optional_str(data.get("slack_event_id")),
            slack_initial_comment=_optional_str(data.get("slack_initial_comment")),
            uploaded_by=_optional_str(data.get("uploaded_by")),
        )


class ArchiveProvider(Protocol):
    """Interface implemented by source archive providers."""

    def save_source(
        self,
        file_path: Path,
        metadata: SourceIngestMetadata,
        *,
        now: datetime | None = None,
    ) -> ArchivedSourceRef:
        """Persist a source file and return its archive reference."""

    def get_source_path(self, ref: ArchivedSourceRef) -> Path:
        """Return the local path for an archived source."""

    def get_source_metadata(self, ref: ArchivedSourceRef) -> ArchivedSourceRef:
        """Return metadata for an archived source."""

    def get_access_url(self, ref: ArchivedSourceRef) -> str:
        """Return a local or remote access URL for an archived source."""

    def exists(self, ref: ArchivedSourceRef) -> bool:
        """Return whether the archived source exists."""

    def list_sources(self) -> Iterable[ArchivedSourceRef]:
        """List archived sources known to the provider."""


class LocalFilesystemArchiveProvider:
    """Archive provider that stores sources on the local filesystem."""

    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path.expanduser()

    def save_source(
        self,
        file_path: Path,
        metadata: SourceIngestMetadata,
        *,
        now: datetime | None = None,
    ) -> ArchivedSourceRef:
        """Archive a source file under a content-addressed local path."""

        source_path = file_path.expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")

        content_hash = hash_file(source_path)
        existing_metadata_path = self._find_existing_metadata(content_hash)
        if existing_metadata_path is not None:
            logger.info(
                "Archive source already exists path=%s content_hash=%s metadata=%s",
                source_path,
                content_hash,
                existing_metadata_path,
            )
            return self._read_metadata(existing_metadata_path)

        created_at = normalize_datetime(now or datetime.now(UTC))
        archive_directory = self._archive_directory(content_hash, created_at)
        original_path = archive_directory / "original"
        metadata_path = archive_directory / "metadata.json"

        archive_directory.mkdir(parents=True, exist_ok=True)
        if not original_path.exists():
            shutil.copyfile(source_path, original_path)

        ref = ArchivedSourceRef(
            archive_provider=ArchiveProviderKind.LOCAL,
            archive_id=self._archive_id(content_hash, created_at),
            uri=str(original_path),
            content_hash=content_hash,
            original_filename=source_path.name,
            mime_type=detect_mime_type(source_path),
            size_bytes=source_path.stat().st_size,
            created_at=created_at,
            ingestion_method=metadata.ingestion_method,
            original_path=metadata.original_path or str(source_path),
            slack_workspace_id=metadata.slack_workspace_id,
            slack_enterprise_id=metadata.slack_enterprise_id,
            slack_team_id=metadata.slack_team_id,
            slack_context_team_id=metadata.slack_context_team_id,
            slack_channel_id=metadata.slack_channel_id,
            slack_channel_name=metadata.slack_channel_name,
            slack_message_ts=metadata.slack_message_ts,
            slack_thread_ts=metadata.slack_thread_ts,
            slack_file_id=metadata.slack_file_id,
            slack_message_permalink=metadata.slack_message_permalink,
            slack_file_permalink=metadata.slack_file_permalink,
            slack_event_id=metadata.slack_event_id,
            slack_initial_comment=metadata.slack_initial_comment,
            uploaded_by=metadata.uploaded_by,
        )
        metadata_path.write_text(
            json.dumps(ref.to_metadata_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Archive source saved path=%s archive_id=%s content_hash=%s size_bytes=%s",
            source_path,
            ref.archive_id,
            ref.content_hash,
            ref.size_bytes,
        )
        return ref

    def get_source_path(self, ref: ArchivedSourceRef) -> Path:
        """Return the archived source path."""

        return Path(ref.uri)

    def get_source_metadata(self, ref: ArchivedSourceRef) -> ArchivedSourceRef:
        """Return stored metadata for an archived source."""

        return self._read_metadata(self._metadata_path(ref))

    def get_access_url(self, ref: ArchivedSourceRef) -> str:
        """Return a file URL for the local archived source."""

        return self.get_source_path(ref).resolve().as_uri()

    def exists(self, ref: ArchivedSourceRef) -> bool:
        """Return whether both the archived source and metadata exist."""

        return (
            self.get_source_path(ref).is_file() and self._metadata_path(ref).is_file()
        )

    def list_sources(self) -> Iterable[ArchivedSourceRef]:
        """List all stored source references."""

        sources_root = self.root_path / "sources"
        if not sources_root.exists():
            return ()
        return tuple(
            self._read_metadata(path)
            for path in sorted(sources_root.glob("*/*/*/metadata.json"))
        )

    def _archive_directory(self, content_hash: str, created_at: datetime) -> Path:
        return self.root_path / self._archive_id(content_hash, created_at)

    def _archive_id(self, content_hash: str, created_at: datetime) -> str:
        return f"sources/{created_at:%Y}/{created_at:%m}/{content_hash}"

    def _metadata_path(self, ref: ArchivedSourceRef) -> Path:
        return self.root_path / ref.archive_id / "metadata.json"

    def _find_existing_metadata(self, content_hash: str) -> Path | None:
        matches = sorted(
            (self.root_path / "sources").glob(f"*/*/{content_hash}/metadata.json")
        )
        if not matches:
            return None
        return matches[0]

    def _read_metadata(self, metadata_path: Path) -> ArchivedSourceRef:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Archive metadata is not an object: {metadata_path}")
        return ArchivedSourceRef.from_metadata_dict(data)


def hash_file(file_path: Path) -> str:
    """Return the SHA-256 hex digest for a file."""

    digest = hashlib.sha256()
    with file_path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_mime_type(file_path: Path) -> str:
    """Detect a file MIME type with a safe binary fallback."""

    mime_type, _encoding = mimetypes.guess_type(file_path.name)
    return mime_type or "application/octet-stream"


def normalize_datetime(value: datetime) -> datetime:
    """Normalize datetimes to timezone-aware UTC values."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_datetime(value: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC timestamp."""

    return normalize_datetime(value).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
