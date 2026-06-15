"""Slack ingestion orchestration over the existing ingest pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from slack_vault.ai import AnthropicAIProvider, RetryingAITextProvider
from slack_vault.archive import SourceIngestMetadata
from slack_vault.config import Settings
from slack_vault.enhancement import AnthropicEvidenceEnhancer, EvidenceEnhancer
from slack_vault.git_vault import GitVaultCommitter, VaultCommitter
from slack_vault.ingest import (
    IngestProcessingError,
    LocalFileIngestResult,
    ingest_file_path,
)
from slack_vault.ops_state import (
    EnqueueJobResult,
    IngestionJob,
    SQLiteOperationalState,
)
from slack_vault.slack_events import normalize_slack_ingestion_events
from slack_vault.slack_files import (
    SlackChatClient,
    SlackFileInfo,
    SlackFilesClient,
    download_slack_file,
    fetch_slack_file_info,
    post_slack_message,
)
from slack_vault.synthesis import AnthropicKnowledgeSynthesizer, KnowledgeSynthesizer

logger = logging.getLogger(__name__)


class SlackWebClient(SlackFilesClient, SlackChatClient, Protocol):
    """Slack WebClient subset used by ingestion."""


class FetchFileInfo(Protocol):
    """Callable for fetching Slack file metadata."""

    def __call__(
        self,
        client: SlackFilesClient,
        *,
        file_id: str,
        team_id: str | None = None,
    ) -> SlackFileInfo:
        """Fetch one Slack file metadata record."""


class DownloadFile(Protocol):
    """Callable for downloading a Slack file."""

    def __call__(
        self,
        file_info: SlackFileInfo,
        *,
        bot_token: str,
        target_directory: Path,
    ) -> Path:
        """Download one Slack file and return the local path."""


class IngestPath(Protocol):
    """Callable for ingesting a downloaded file path."""

    def __call__(
        self,
        file_path: Path,
        settings: Settings,
        *,
        metadata: SourceIngestMetadata,
        overwrite_source_record: bool = False,
        evidence_enhancer: EvidenceEnhancer | None = None,
        knowledge_synthesizer: KnowledgeSynthesizer | None = None,
        vault_committer: VaultCommitter | None = None,
        now: datetime | None = None,
    ) -> LocalFileIngestResult:
        """Ingest a local file path."""


@dataclass(frozen=True)
class SlackIngestDependencies:
    """Runtime dependencies for one Slack ingestion job."""

    evidence_enhancer: EvidenceEnhancer | None
    knowledge_synthesizer: KnowledgeSynthesizer | None
    vault_committer: VaultCommitter | None


class BuildDependencies(Protocol):
    """Callable for creating live ingest dependencies from settings."""

    def __call__(self, settings: Settings) -> SlackIngestDependencies:
        """Build dependencies for one job."""


@dataclass(frozen=True)
class SlackIngestionService:
    """Service that enqueues and processes Slack ingestion jobs."""

    settings: Settings
    state: SQLiteOperationalState
    slack_client: SlackWebClient
    download_root_path: Path
    fetch_file_info: FetchFileInfo = fetch_slack_file_info
    download_file: DownloadFile = download_slack_file
    ingest_path: IngestPath = ingest_file_path
    build_dependencies: BuildDependencies = lambda settings: (
        default_slack_ingest_dependencies(settings)
    )

    def handle_event_payload(
        self,
        payload: dict[str, object],
    ) -> tuple[EnqueueJobResult, ...]:
        """Normalize a Slack payload and enqueue ingestion jobs."""

        ingestion_channel_id = self.settings.slack.ingestion_channel_id
        if ingestion_channel_id is None:
            raise ValueError("SLACK_VAULT_INGESTION_CHANNEL_ID is required")

        results: list[EnqueueJobResult] = []
        events = normalize_slack_ingestion_events(
            payload,
            ingestion_channel_id=ingestion_channel_id,
            allow_external_shared_channels=(
                self.settings.slack.allow_external_shared_channels
            ),
        )
        for event in events:
            self.state.record_slack_event(event)
            result = self.state.enqueue_ingestion_job(event)
            results.append(result)
            if result.created:
                post_slack_message(
                    self.slack_client,
                    channel_id=event.channel_id,
                    thread_ts=event.thread_ts or event.message_ts,
                    text=f"Queued Slack Vault ingestion for `{event.file_id}`.",
                )
        return tuple(results)

    def process_next_job(self) -> IngestionJob | None:
        """Process the next queued Slack ingestion job, if any."""

        job = self.state.claim_next_queued_job()
        if job is None:
            return None

        try:
            bot_token = self._bot_token()
            file_info = self.fetch_file_info(
                self.slack_client,
                file_id=job.file_id,
                team_id=job.team_id,
            )
            self.state.update_job_file_info(
                job.job_id,
                file_name=file_info.name,
                file_mime_type=file_info.mimetype,
                file_size_bytes=file_info.size_bytes,
            )
            downloaded_path = self.download_file(
                file_info,
                bot_token=bot_token,
                target_directory=self.download_root_path / job.job_id,
            )
            dependencies = self.build_dependencies(self.settings)
            if dependencies.vault_committer is not None:
                dependencies.vault_committer.ensure_clean_worktree(
                    self.settings.obsidian_vault_path
                )
            ingest_result = self.ingest_path(
                downloaded_path,
                self.settings,
                metadata=_source_metadata(job, file_info, self.settings),
                evidence_enhancer=dependencies.evidence_enhancer,
                knowledge_synthesizer=dependencies.knowledge_synthesizer,
                vault_committer=dependencies.vault_committer,
            )
            knowledge_note_paths = _knowledge_note_paths(ingest_result)
            result_message_ts = post_slack_message(
                self.slack_client,
                channel_id=job.channel_id,
                thread_ts=_thread_ts(job),
                text=_success_message(ingest_result, knowledge_note_paths),
            )
            self.state.mark_job_succeeded(
                job.job_id,
                source_id=ingest_result.source_record.source_id,
                source_record_path=str(ingest_result.source_record.path),
                knowledge_note_paths=tuple(str(path) for path in knowledge_note_paths),
                git_commit_hash=None
                if ingest_result.git_commit is None
                else ingest_result.git_commit.commit_hash,
                slack_result_message_ts=result_message_ts,
            )
        except IngestProcessingError as exc:
            self._mark_and_report_failure(
                job,
                error_stage=exc.stage,
                error_message=exc.reason,
            )
        except Exception as exc:
            logger.exception("Slack ingestion job failed job_id=%s", job.job_id)
            self._mark_and_report_failure(
                job,
                error_stage="slack_ingest",
                error_message=str(exc),
            )

        refreshed = self.state.get_job(job.job_id)
        if refreshed is None:
            raise RuntimeError(f"Processed job disappeared: {job.job_id}")
        return refreshed

    def _mark_and_report_failure(
        self,
        job: IngestionJob,
        *,
        error_stage: str,
        error_message: str,
    ) -> str | None:
        text = (
            "Slack Vault ingestion failed "
            f"for `{job.file_id}` during `{error_stage}`: {error_message}"
        )
        result_message_ts = post_slack_message(
            self.slack_client,
            channel_id=job.channel_id,
            thread_ts=_thread_ts(job),
            text=text,
        )
        self.state.mark_job_failed(
            job.job_id,
            error_stage=error_stage,
            error_message=error_message,
            slack_result_message_ts=result_message_ts,
        )
        return result_message_ts

    def _bot_token(self) -> str:
        if self.settings.slack.bot_token is None:
            raise ValueError("SLACK_BOT_TOKEN is required for Slack file download")
        return self.settings.slack.bot_token


def default_slack_ingest_dependencies(settings: Settings) -> SlackIngestDependencies:
    """Build live ingestion dependencies from runtime settings."""

    evidence_enhancer: EvidenceEnhancer | None = None
    knowledge_synthesizer: KnowledgeSynthesizer | None = None
    ai_provider: RetryingAITextProvider | None = None
    if settings.ingestion.slack_ingest_enhance or (
        settings.ingestion.slack_ingest_synthesize
    ):
        ai_provider = RetryingAITextProvider(
            AnthropicAIProvider.from_settings(settings),
            retry=settings.ai.retry,
        )
    if settings.ingestion.slack_ingest_enhance:
        if ai_provider is None:
            raise ValueError("AI provider is required for Slack evidence enhancement")
        evidence_enhancer = AnthropicEvidenceEnhancer(ai_provider)
    if settings.ingestion.slack_ingest_synthesize:
        if ai_provider is None:
            raise ValueError("AI provider is required for Slack knowledge synthesis")
        knowledge_synthesizer = AnthropicKnowledgeSynthesizer(ai_provider)
    vault_committer: VaultCommitter | None = (
        GitVaultCommitter(
            push_after_commit=settings.ingestion.slack_ingest_git_push,
        )
        if settings.ingestion.slack_ingest_git_commit
        else None
    )
    return SlackIngestDependencies(
        evidence_enhancer=evidence_enhancer,
        knowledge_synthesizer=knowledge_synthesizer,
        vault_committer=vault_committer,
    )


def build_slack_ingestion_service(
    settings: Settings,
    *,
    slack_client: SlackWebClient,
) -> SlackIngestionService:
    """Build the default Slack ingestion service."""

    return SlackIngestionService(
        settings=settings,
        state=SQLiteOperationalState(settings.operational.db_path),
        slack_client=slack_client,
        download_root_path=settings.operational.db_path.parent / "slack-downloads",
    )


def _source_metadata(
    job: IngestionJob,
    file_info: SlackFileInfo,
    settings: Settings,
) -> SourceIngestMetadata:
    return SourceIngestMetadata(
        ingestion_method="slack_file",
        original_path=f"slack://{job.team_id or 'unknown-team'}/{job.file_id}",
        slack_workspace_id=job.team_id,
        slack_enterprise_id=job.enterprise_id,
        slack_team_id=job.team_id,
        slack_context_team_id=job.context_team_id,
        slack_channel_id=job.channel_id,
        slack_channel_name=settings.slack.ingestion_channel_name,
        slack_message_ts=job.message_ts,
        slack_thread_ts=job.thread_ts,
        slack_file_id=job.file_id,
        slack_file_permalink=file_info.permalink,
        slack_event_id=job.slack_event_id,
        slack_initial_comment=job.initial_comment,
        uploaded_by=job.user_id or file_info.user_id,
    )


def _knowledge_note_paths(result: LocalFileIngestResult) -> tuple[Path, ...]:
    if result.synthesis_result is None or result.synthesis_result.note is None:
        return ()
    return (result.synthesis_result.note.path,)


def _success_message(
    result: LocalFileIngestResult,
    knowledge_note_paths: tuple[Path, ...],
) -> str:
    lines = [
        "Slack Vault ingestion succeeded.",
        f"Source: `{result.source_record.source_id}`",
        f"Source record: `{result.source_record.path}`",
    ]
    if knowledge_note_paths:
        lines.append(
            "Knowledge notes: "
            + ", ".join(f"`{path}`" for path in knowledge_note_paths)
        )
    if result.git_commit is None:
        lines.append("Vault Git commit: not requested")
    elif result.git_commit.committed:
        lines.append(f"Vault Git commit: `{result.git_commit.commit_hash}`")
        if result.git_commit.pushed:
            lines.append("Vault Git push: pushed")
        else:
            lines.append(
                f"Vault Git push: not pushed ({result.git_commit.push_skipped_reason})"
            )
    else:
        lines.append(f"Vault Git commit: skipped ({result.git_commit.skipped_reason})")
    return "\n".join(lines)


def _thread_ts(job: IngestionJob) -> str:
    return job.thread_ts or job.message_ts
