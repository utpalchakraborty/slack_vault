from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slack_vault.ops_state import IngestionJobStatus, SQLiteOperationalState
from slack_vault.slack_events import SlackIngestionEvent


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


def _event() -> SlackIngestionEvent:
    return SlackIngestionEvent(
        event_id="Ev123",
        event_type="message",
        enterprise_id="E123",
        team_id="T123",
        context_team_id="TCTX",
        is_enterprise_install=True,
        channel_id="C123",
        user_id="W123",
        event_ts="1718300000.000200",
        message_ts="1718300000.000100",
        thread_ts="1718300000.000100",
        file_id="F123",
        initial_comment="Please ingest this.",
        is_ext_shared_channel=False,
        raw_payload={"event_id": "Ev123"},
    )
