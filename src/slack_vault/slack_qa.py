"""Slack Q&A orchestration over the local vault question-answering service."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from slack_vault.config import Settings
from slack_vault.ops_state import EnqueueQAJobResult, QAJob, SQLiteOperationalState
from slack_vault.qa import AnswerCitation, AnswerResult
from slack_vault.qa_service import answer_question_from_settings
from slack_vault.slack_events import normalize_slack_qa_events
from slack_vault.slack_files import SlackChatClient, post_slack_message

logger = logging.getLogger(__name__)

CHECKING_VAULT_MESSAGE = "Checking the vault for relevant notes..."


class AnswerQuestion(Protocol):
    """Callable for answering one local vault question."""

    def __call__(
        self,
        settings: Settings,
        question: str,
        *,
        limit: int,
    ) -> AnswerResult:
        """Answer one question from vault context."""
        ...


@dataclass(frozen=True)
class SlackQuestionAnsweringService:
    """Service that enqueues and processes Slack Q&A jobs."""

    settings: Settings
    state: SQLiteOperationalState
    slack_client: SlackChatClient
    answer_question: AnswerQuestion = answer_question_from_settings
    qa_limit: int = 5

    def handle_event_payload(
        self,
        payload: dict[str, object],
    ) -> tuple[EnqueueQAJobResult, ...]:
        """Normalize a Slack payload and enqueue Q&A jobs."""

        results: list[EnqueueQAJobResult] = []
        events = normalize_slack_qa_events(
            payload,
            allow_external_shared_channels=(
                self.settings.slack.allow_external_shared_channels
            ),
        )
        for event in events:
            self.state.record_slack_qa_event(event)
            result = self.state.enqueue_qa_job(event)
            results.append(result)
            if result.created:
                message_ts = post_slack_message(
                    self.slack_client,
                    channel_id=event.channel_id,
                    thread_ts=event.thread_ts,
                    text=CHECKING_VAULT_MESSAGE,
                )
                self.state.update_qa_job_initial_message_ts(
                    result.job.job_id,
                    slack_initial_message_ts=message_ts,
                )
        return tuple(results)

    def process_next_job(self) -> QAJob | None:
        """Process the next queued Slack Q&A job, if any."""

        job = self.state.claim_next_queued_qa_job()
        if job is None:
            return None

        try:
            result = self.answer_question(
                self.settings,
                job.question_text,
                limit=self.qa_limit,
            )
            result_message_ts = post_slack_message(
                self.slack_client,
                channel_id=job.channel_id,
                thread_ts=job.thread_ts,
                text=render_slack_answer_result(result),
            )
            self.state.mark_qa_job_succeeded(
                job.job_id,
                search_query=result.context.search_query,
                answer_text=result.answer,
                citations_json=_citations_json(result.citations),
                slack_result_message_ts=result_message_ts,
            )
        except Exception as exc:
            logger.exception("Slack Q&A job failed job_id=%s", job.job_id)
            self._mark_and_report_failure(
                job,
                error_stage="slack_qa",
                error_message=str(exc),
            )

        refreshed = self.state.get_qa_job(job.job_id)
        if refreshed is None:
            raise RuntimeError(f"Processed Q&A job disappeared: {job.job_id}")
        return refreshed

    def _mark_and_report_failure(
        self,
        job: QAJob,
        *,
        error_stage: str,
        error_message: str,
    ) -> str | None:
        text = (
            "Slack Vault Q&A failed while checking the vault "
            f"during `{error_stage}`: {error_message}"
        )
        result_message_ts = post_slack_message(
            self.slack_client,
            channel_id=job.channel_id,
            thread_ts=job.thread_ts,
            text=text,
        )
        self.state.mark_qa_job_failed(
            job.job_id,
            error_stage=error_stage,
            error_message=error_message,
            slack_result_message_ts=result_message_ts,
        )
        return result_message_ts


def build_slack_qa_service(
    settings: Settings,
    *,
    slack_client: SlackChatClient,
) -> SlackQuestionAnsweringService:
    """Build the default Slack Q&A service."""

    return SlackQuestionAnsweringService(
        settings=settings,
        state=SQLiteOperationalState(settings.operational.db_path),
        slack_client=slack_client,
    )


def render_slack_answer_result(result: AnswerResult) -> str:
    """Render an answer result for Slack mrkdwn."""

    lines = [_escape_slack_text(result.answer)]
    if result.citations:
        lines.extend(("", "Evidence:"))
        lines.extend(_render_slack_citation(citation) for citation in result.citations)
    return "\n".join(lines)


def _render_slack_citation(citation: AnswerCitation) -> str:
    parts = [
        f"[{citation.citation_id}]",
        f"*{_escape_slack_text(citation.note_title)}*",
        f"`{_escape_slack_text(citation.note_path.as_posix())}`",
    ]
    if citation.source_ids:
        source_ids = ", ".join(
            f"`{_escape_slack_text(source_id)}`" for source_id in citation.source_ids
        )
        parts.append(f"Source: {source_ids}")
    if citation.source_record_paths:
        records = ", ".join(
            f"`{_escape_slack_text(path.as_posix())}`"
            for path in citation.source_record_paths
        )
        parts.append(f"Records: {records}")
    return " ".join(parts)


def _citations_json(citations: tuple[AnswerCitation, ...]) -> str:
    payload = [
        {
            "citation_id": citation.citation_id,
            "note_title": citation.note_title,
            "note_path": citation.note_path.as_posix(),
            "source_ids": list(citation.source_ids),
            "source_record_paths": [
                path.as_posix() for path in citation.source_record_paths
            ],
        }
        for citation in citations
    ]
    return json.dumps(payload, sort_keys=True)


def _escape_slack_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
