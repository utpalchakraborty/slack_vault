"""Source record IDs and Markdown rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef, format_datetime

SOURCE_RECORDS_DIRECTORY = Path("20 Sources/sources")


@dataclass(frozen=True)
class SourceRecordWriteResult:
    """Result of writing a source record to the vault."""

    source_id: str
    path: Path
    created: bool


def generate_source_id(ref: ArchivedSourceRef) -> str:
    """Generate a stable source ID for an archived source."""

    return f"source-{ref.created_at:%Y-%m-%d}-{ref.content_hash[:12]}"


def source_record_path(vault_path: Path, source_id: str) -> Path:
    """Return the vault path for a source record."""

    return vault_path / SOURCE_RECORDS_DIRECTORY / f"{source_id}.md"


def write_source_record(
    vault_path: Path,
    ref: ArchivedSourceRef,
    *,
    overwrite: bool = False,
) -> SourceRecordWriteResult:
    """Write a Markdown source record into the Obsidian vault."""

    source_id = generate_source_id(ref)
    target_path = source_record_path(vault_path, source_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists() and not overwrite:
        return SourceRecordWriteResult(
            source_id=source_id, path=target_path, created=False
        )

    target_path.write_text(render_source_record(ref, source_id), encoding="utf-8")
    return SourceRecordWriteResult(source_id=source_id, path=target_path, created=True)


def render_source_record(ref: ArchivedSourceRef, source_id: str) -> str:
    """Render an archived source reference as Markdown."""

    frontmatter = _frontmatter(
        {
            "title": ref.original_filename,
            "type": "source_record",
            "source_id": source_id,
            "archive_provider": ref.archive_provider.value,
            "archive_id": ref.archive_id,
            "archive_uri": ref.uri,
            "content_hash": ref.content_hash,
            "mime_type": ref.mime_type,
            "size_bytes": ref.size_bytes,
            "original_filename": ref.original_filename,
            "ingestion_method": ref.ingestion_method,
            "ingested_at": format_datetime(ref.created_at),
            "extraction_status": "pending",
            "uploaded_by": ref.uploaded_by,
            "slack_workspace_id": ref.slack_workspace_id,
            "slack_channel_id": ref.slack_channel_id,
            "slack_message_ts": ref.slack_message_ts,
            "slack_file_id": ref.slack_file_id,
            "original_path": ref.original_path,
        }
    )

    origin_lines = [
        f"- Ingestion method: {ref.ingestion_method}",
        f"- Original filename: {ref.original_filename}",
    ]
    if ref.original_path is not None:
        origin_lines.append(f"- Local source path: `{ref.original_path}`")
    if ref.uploaded_by is not None:
        origin_lines.append(f"- Uploaded by: {ref.uploaded_by}")
    if ref.slack_channel_id is not None:
        origin_lines.append(f"- Slack channel: {ref.slack_channel_id}")
    if ref.slack_message_ts is not None:
        origin_lines.append(f"- Slack message timestamp: {ref.slack_message_ts}")

    return "\n".join(
        [
            frontmatter,
            f"# {ref.original_filename}",
            "",
            "## Origin",
            "",
            *origin_lines,
            "",
            "## Archive",
            "",
            f"- Provider: {ref.archive_provider.value}",
            f"- Archive ID: `{ref.archive_id}`",
            f"- URI: `{ref.uri}`",
            f"- Content hash: `{ref.content_hash}`",
            f"- MIME type: `{ref.mime_type}`",
            f"- Size: {ref.size_bytes} bytes",
            f"- Ingested at: {format_datetime(ref.created_at)}",
            "",
            "## Extracted Evidence",
            "",
            "Extraction has not run yet.",
            "",
        ]
    )


def _frontmatter(values: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))
