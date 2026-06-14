"""Source record IDs and Markdown rendering."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef, format_datetime
from slack_vault.enhancement import EnhancementResult
from slack_vault.extraction import ExtractionResult

logger = logging.getLogger(__name__)

SOURCE_RECORDS_DIRECTORY = Path("20 Sources/sources")
ENHANCEMENT_NOT_REQUESTED = "not_requested"


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
    extraction_result: ExtractionResult | None = None,
    enhancement_result: EnhancementResult | None = None,
    evidence_artifact_uri: str | None = None,
    evidence_artifact_schema: str | None = None,
    overwrite: bool = False,
) -> SourceRecordWriteResult:
    """Write a Markdown source record into the Obsidian vault."""

    source_id = generate_source_id(ref)
    target_path = source_record_path(vault_path, source_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists() and not overwrite:
        logger.info(
            "Source record exists source_id=%s path=%s overwrite=false",
            source_id,
            target_path,
        )
        return SourceRecordWriteResult(
            source_id=source_id, path=target_path, created=False
        )

    logger.info(
        "Writing source record source_id=%s path=%s overwrite=%s",
        source_id,
        target_path,
        overwrite,
    )
    target_path.write_text(
        render_source_record(
            ref,
            source_id,
            extraction_result=extraction_result,
            enhancement_result=enhancement_result,
            evidence_artifact_uri=evidence_artifact_uri,
            evidence_artifact_schema=evidence_artifact_schema,
        ),
        encoding="utf-8",
    )
    return SourceRecordWriteResult(source_id=source_id, path=target_path, created=True)


def render_source_record(
    ref: ArchivedSourceRef,
    source_id: str,
    *,
    extraction_result: ExtractionResult | None = None,
    enhancement_result: EnhancementResult | None = None,
    evidence_artifact_uri: str | None = None,
    evidence_artifact_schema: str | None = None,
) -> str:
    """Render an archived source reference as Markdown."""

    extraction_status = (
        "pending" if extraction_result is None else extraction_result.status.value
    )
    enhancement_status = (
        ENHANCEMENT_NOT_REQUESTED
        if enhancement_result is None
        else enhancement_result.status.value
    )
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
            "extraction_status": extraction_status,
            "extractor_name": None
            if extraction_result is None
            else extraction_result.extractor_name,
            "extracted_evidence_count": None
            if extraction_result is None
            else len(extraction_result.evidence),
            "extraction_error": None
            if extraction_result is None
            else extraction_result.error_message,
            "enhancement_status": enhancement_status,
            "enhancer_name": None
            if enhancement_result is None
            else enhancement_result.enhancer_name,
            "enhanced_evidence_count": None
            if enhancement_result is None
            else len(enhancement_result.enhanced_evidence),
            "enhancement_error": None
            if enhancement_result is None
            else enhancement_result.error_message,
            "enhancement_model": None
            if enhancement_result is None
            else enhancement_result.model,
            "enhancement_input_tokens": None
            if enhancement_result is None
            else enhancement_result.input_tokens,
            "enhancement_output_tokens": None
            if enhancement_result is None
            else enhancement_result.output_tokens,
            "enhancement_cache_creation_input_tokens": None
            if enhancement_result is None
            else enhancement_result.cache_creation_input_tokens,
            "enhancement_cache_read_input_tokens": None
            if enhancement_result is None
            else enhancement_result.cache_read_input_tokens,
            "evidence_artifact_uri": evidence_artifact_uri,
            "evidence_artifact_schema": evidence_artifact_schema,
            "uploaded_by": ref.uploaded_by,
            "slack_workspace_id": ref.slack_workspace_id,
            "slack_enterprise_id": ref.slack_enterprise_id,
            "slack_team_id": ref.slack_team_id,
            "slack_context_team_id": ref.slack_context_team_id,
            "slack_channel_id": ref.slack_channel_id,
            "slack_channel_name": ref.slack_channel_name,
            "slack_message_ts": ref.slack_message_ts,
            "slack_thread_ts": ref.slack_thread_ts,
            "slack_file_id": ref.slack_file_id,
            "slack_message_permalink": ref.slack_message_permalink,
            "slack_file_permalink": ref.slack_file_permalink,
            "slack_event_id": ref.slack_event_id,
            "slack_initial_comment": ref.slack_initial_comment,
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
    if ref.slack_enterprise_id is not None:
        origin_lines.append(f"- Slack Enterprise: {ref.slack_enterprise_id}")
    if ref.slack_team_id is not None:
        origin_lines.append(f"- Slack team: {ref.slack_team_id}")
    if ref.slack_channel_id is not None:
        origin_lines.append(f"- Slack channel: {ref.slack_channel_id}")
    if ref.slack_channel_name is not None:
        origin_lines.append(f"- Slack channel name: {ref.slack_channel_name}")
    if ref.slack_message_ts is not None:
        origin_lines.append(f"- Slack message timestamp: {ref.slack_message_ts}")
    if ref.slack_thread_ts is not None:
        origin_lines.append(f"- Slack thread timestamp: {ref.slack_thread_ts}")
    if ref.slack_message_permalink is not None:
        origin_lines.append(f"- Slack message: {ref.slack_message_permalink}")
    if ref.slack_file_permalink is not None:
        origin_lines.append(f"- Slack file: {ref.slack_file_permalink}")

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
            *_render_extracted_evidence(
                extraction_result,
                evidence_artifact_uri=evidence_artifact_uri,
            ),
            "",
            "## Enhanced Evidence",
            "",
            *_render_enhanced_evidence(
                enhancement_result,
                evidence_artifact_uri=evidence_artifact_uri,
            ),
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


def _render_extracted_evidence(
    extraction_result: ExtractionResult | None,
    *,
    evidence_artifact_uri: str | None,
) -> list[str]:
    if extraction_result is None:
        return ["Extraction has not run yet."]

    lines = [
        f"- Status: {extraction_result.status.value}",
        f"- Extractor: {extraction_result.extractor_name}",
        f"- Evidence blocks: {len(extraction_result.evidence)}",
    ]
    if extraction_result.error_message is not None:
        lines.append(f"- Error: {extraction_result.error_message}")
    if evidence_artifact_uri is not None:
        lines.append(f"- Full evidence artifact: `{evidence_artifact_uri}`")
    lines.append("- Full extracted evidence is stored outside the Git-backed vault.")
    return lines


def _render_enhanced_evidence(
    enhancement_result: EnhancementResult | None,
    *,
    evidence_artifact_uri: str | None,
) -> list[str]:
    if enhancement_result is None:
        return ["AI enhancement has not been requested."]

    lines = [
        f"- Status: {enhancement_result.status.value}",
        f"- Enhancer: {enhancement_result.enhancer_name}",
        f"- Enhanced evidence blocks: {len(enhancement_result.enhanced_evidence)}",
    ]
    if enhancement_result.model is not None:
        lines.append(f"- Model: {enhancement_result.model}")
    if enhancement_result.error_message is not None:
        lines.append(f"- Error: {enhancement_result.error_message}")
    if evidence_artifact_uri is not None:
        lines.append(f"- Full evidence artifact: `{evidence_artifact_uri}`")
    lines.append("- Full enhanced evidence is stored outside the Git-backed vault.")
    return lines


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))
