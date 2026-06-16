from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from slack_vault.ops_state import (
    IngestionJobStatus,
    QAJobStatus,
    SQLiteOperationalState,
)
from slack_vault.slack_events import SlackIngestionEvent, SlackQAEvent


def test_sqlite_state_records_events_and_enqueues_idempotent_jobs(
    tmp_path: Path,
) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    event = _event()

    first_event = state.record_slack_event(
        event,
        received_at=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
    )
    second_event = state.record_slack_event(
        event,
        received_at=datetime(2026, 6, 14, 12, 1, tzinfo=UTC),
    )
    first_job = state.enqueue_ingestion_job(
        event,
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
    )
    second_job = state.enqueue_ingestion_job(
        event,
        created_at=datetime(2026, 6, 14, 12, 1, tzinfo=UTC),
    )

    assert first_event is True
    assert second_event is False
    assert first_job.created is True
    assert second_job.created is False
    assert second_job.job.job_id == first_job.job.job_id
    assert state.list_jobs() == (first_job.job,)


def test_sqlite_state_collapses_message_and_file_shared_events_for_same_file(
    tmp_path: Path,
) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    file_shared = _event(
        event_id="Ev-file-shared",
        event_type="file_shared",
        event_ts="1718300000.000100",
        message_ts="1718300000.000100",
        thread_ts=None,
    )
    message = _event(
        event_id="Ev-message",
        event_type="message",
        event_ts="1718300000.665019",
        message_ts="1718300000.665019",
        thread_ts="1718300000.665019",
    )

    first = state.enqueue_ingestion_job(file_shared)
    second = state.enqueue_ingestion_job(message)

    assert first.created is True
    assert second.created is False
    assert second.job.job_id == first.job.job_id
    assert state.list_jobs() == (first.job,)


def test_sqlite_state_ignores_legacy_queued_duplicate_after_success(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    state = SQLiteOperationalState(db_path)
    state.initialize()
    _insert_job(
        db_path,
        job_id="job-succeeded",
        status=IngestionJobStatus.SUCCEEDED,
        slack_event_id="Ev-file-shared",
        dedupe_key="|T123|C123|1718300000.000100|F123",
        event_ts="1718300000.000100",
        message_ts="1718300000.000100",
        thread_ts=None,
        created_at="2026-06-14T12:00:00Z",
        started_at="2026-06-14T12:01:00Z",
        finished_at="2026-06-14T12:02:00Z",
    )
    _insert_job(
        db_path,
        job_id="job-duplicate",
        status=IngestionJobStatus.QUEUED,
        slack_event_id="Ev-message",
        dedupe_key="|T123|C123|1718300000.665019|F123",
        event_ts="1718300000.665019",
        message_ts="1718300000.665019",
        thread_ts="1718300000.665019",
        created_at="2026-06-14T12:03:00Z",
    )

    claimed = state.claim_next_queued_job(
        started_at=datetime(2026, 6, 14, 12, 4, tzinfo=UTC),
    )
    duplicate = state.get_job("job-duplicate")

    assert claimed is None
    assert duplicate is not None
    assert duplicate.status is IngestionJobStatus.IGNORED
    assert duplicate.error_stage == "duplicate_slack_file"
    assert duplicate.error_message == "Duplicate Slack file event for job job-succeeded"
    assert duplicate.finished_at == "2026-06-14T12:04:00Z"


def test_sqlite_state_claims_and_marks_success(tmp_path: Path) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    event = _event()
    state.record_slack_event(event)
    enqueued = state.enqueue_ingestion_job(event)

    claimed = state.claim_next_queued_job(
        started_at=datetime(2026, 6, 14, 12, 2, tzinfo=UTC),
    )

    assert claimed is not None
    assert claimed.job_id == enqueued.job.job_id
    assert claimed.status is IngestionJobStatus.RUNNING
    assert claimed.started_at == "2026-06-14T12:02:00Z"

    state.update_job_file_info(
        claimed.job_id,
        file_name="plan.md",
        file_mime_type="text/markdown",
        file_size_bytes=123,
    )
    state.mark_job_succeeded(
        claimed.job_id,
        source_id="source-123",
        source_record_path="20 Sources/sources/source-123.md",
        knowledge_note_paths=("10 Knowledge/plan.md",),
        git_commit_hash="abc123",
        slack_result_message_ts="1718300001.000100",
        finished_at=datetime(2026, 6, 14, 12, 3, tzinfo=UTC),
    )

    completed = state.get_job(claimed.job_id)

    assert completed is not None
    assert completed.status is IngestionJobStatus.SUCCEEDED
    assert completed.file_name == "plan.md"
    assert completed.file_mime_type == "text/markdown"
    assert completed.file_size_bytes == 123
    assert completed.source_id == "source-123"
    assert completed.knowledge_note_paths == ("10 Knowledge/plan.md",)
    assert completed.git_commit_hash == "abc123"
    assert completed.slack_result_message_ts == "1718300001.000100"
    assert completed.finished_at == "2026-06-14T12:03:00Z"


def test_sqlite_state_marks_failure(tmp_path: Path) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    event = _event()
    state.record_slack_event(event)
    job = state.enqueue_ingestion_job(event).job

    state.mark_job_failed(
        job.job_id,
        error_stage="download",
        error_message="not visible",
    )

    failed = state.get_job(job.job_id)

    assert failed is not None
    assert failed.status is IngestionJobStatus.FAILED
    assert failed.error_stage == "download"
    assert failed.error_message == "not visible"


def test_sqlite_state_records_qa_events_and_enqueues_idempotent_jobs(
    tmp_path: Path,
) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    event = _qa_event()

    first_event = state.record_slack_qa_event(
        event,
        received_at=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
    )
    second_event = state.record_slack_qa_event(
        event,
        received_at=datetime(2026, 6, 14, 12, 1, tzinfo=UTC),
    )
    first_job = state.enqueue_qa_job(
        event,
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
    )
    second_job = state.enqueue_qa_job(
        event,
        created_at=datetime(2026, 6, 14, 12, 1, tzinfo=UTC),
    )

    assert first_event is True
    assert second_event is False
    assert first_job.created is True
    assert second_job.created is False
    assert second_job.job.job_id == first_job.job.job_id
    assert first_job.job.question_text == "What does Project Alpha need?"
    assert state.list_qa_jobs() == (first_job.job,)


def test_sqlite_state_claims_and_marks_qa_success(tmp_path: Path) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    job = state.enqueue_qa_job(_qa_event()).job
    state.update_qa_job_initial_message_ts(
        job.job_id,
        slack_initial_message_ts="1718300001.000100",
    )

    claimed = state.claim_next_queued_qa_job(
        started_at=datetime(2026, 6, 14, 12, 2, tzinfo=UTC),
    )

    assert claimed is not None
    assert claimed.status is QAJobStatus.RUNNING
    assert claimed.started_at == "2026-06-14T12:02:00Z"
    assert claimed.slack_initial_message_ts == "1718300001.000100"

    state.mark_qa_job_succeeded(
        claimed.job_id,
        search_query="Project Alpha",
        answer_text="Project Alpha needs local-first ingest [1].",
        citations_json='[{"citation_id": 1}]',
        slack_result_message_ts="1718300002.000100",
        finished_at=datetime(2026, 6, 14, 12, 3, tzinfo=UTC),
    )
    completed = state.get_qa_job(claimed.job_id)

    assert completed is not None
    assert completed.status is QAJobStatus.SUCCEEDED
    assert completed.search_query == "Project Alpha"
    assert completed.answer_text == "Project Alpha needs local-first ingest [1]."
    assert completed.citations_json == '[{"citation_id": 1}]'
    assert completed.slack_result_message_ts == "1718300002.000100"
    assert completed.finished_at == "2026-06-14T12:03:00Z"


def test_sqlite_state_marks_qa_failure(tmp_path: Path) -> None:
    state = SQLiteOperationalState(tmp_path / "state.sqlite3")
    job = state.enqueue_qa_job(_qa_event()).job

    state.mark_qa_job_failed(
        job.job_id,
        error_stage="slack_qa",
        error_message="rate limited",
    )

    failed = state.get_qa_job(job.job_id)

    assert failed is not None
    assert failed.status is QAJobStatus.FAILED
    assert failed.error_stage == "slack_qa"
    assert failed.error_message == "rate limited"


def _event(
    *,
    event_id: str = "Ev123",
    event_type: str = "message",
    event_ts: str = "1718300000.000200",
    message_ts: str = "1718300000.000100",
    thread_ts: str | None = "1718300000.000100",
) -> SlackIngestionEvent:
    return SlackIngestionEvent(
        event_id=event_id,
        event_type=event_type,
        enterprise_id="E123",
        team_id="T123",
        context_team_id="TCTX",
        is_enterprise_install=True,
        channel_id="C123",
        user_id="W123",
        event_ts=event_ts,
        message_ts=message_ts,
        thread_ts=thread_ts,
        file_id="F123",
        initial_comment="Please ingest this.",
        is_ext_shared_channel=False,
        raw_payload={"event_id": event_id},
    )


def _qa_event() -> SlackQAEvent:
    return SlackQAEvent(
        event_id="Ev-qa",
        event_type="message.im",
        enterprise_id="E123",
        team_id="T123",
        context_team_id="TCTX",
        is_enterprise_install=True,
        channel_id="D123",
        channel_type="im",
        user_id="W123",
        event_ts="1718300000.000200",
        message_ts="1718300000.000100",
        thread_ts=None,
        question_text="What does Project Alpha need?",
        is_ext_shared_channel=False,
        raw_payload={"event_id": "Ev-qa"},
    )


def _insert_job(
    db_path: Path,
    *,
    job_id: str,
    status: IngestionJobStatus,
    slack_event_id: str,
    dedupe_key: str,
    event_ts: str,
    message_ts: str,
    thread_ts: str | None,
    created_at: str,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO ingestion_jobs (
                job_id,
                status,
                slack_event_id,
                dedupe_key,
                enterprise_id,
                team_id,
                context_team_id,
                channel_id,
                user_id,
                event_ts,
                message_ts,
                thread_ts,
                file_id,
                initial_comment,
                created_at,
                started_at,
                finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                status.value,
                slack_event_id,
                dedupe_key,
                "E123",
                "T123",
                "TCTX",
                "C123",
                "W123",
                event_ts,
                message_ts,
                thread_ts,
                "F123",
                "Please ingest this.",
                created_at,
                started_at,
                finished_at,
            ),
        )
