"""SQLite operational state for Slack ingestion."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from slack_vault.archive import format_datetime
from slack_vault.slack_events import SlackIngestionEvent


class IngestionJobStatus(StrEnum):
    """Operational status for a Slack ingestion job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    IGNORED = "ignored"


@dataclass(frozen=True)
class IngestionJob:
    """Persisted Slack ingestion job."""

    job_id: str
    status: IngestionJobStatus
    slack_event_id: str
    dedupe_key: str
    enterprise_id: str | None
    team_id: str | None
    context_team_id: str | None
    channel_id: str
    user_id: str | None
    event_ts: str
    message_ts: str
    thread_ts: str | None
    file_id: str
    initial_comment: str | None
    file_name: str | None
    file_mime_type: str | None
    file_size_bytes: int | None
    source_id: str | None
    source_record_path: str | None
    knowledge_note_paths: tuple[str, ...]
    git_commit_hash: str | None
    slack_result_message_ts: str | None
    error_stage: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class EnqueueJobResult:
    """Result of trying to enqueue a Slack ingestion job."""

    job: IngestionJob
    created: bool


class SQLiteOperationalState:
    """SQLite-backed store for Slack event and ingestion-job state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser()

    def initialize(self) -> None:
        """Create operational tables if they do not already exist."""

        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS slack_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    enterprise_id TEXT,
                    team_id TEXT,
                    context_team_id TEXT,
                    channel_id TEXT,
                    user_id TEXT,
                    event_ts TEXT NOT NULL,
                    message_ts TEXT,
                    thread_ts TEXT,
                    file_id TEXT,
                    raw_payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    duplicate_of_event_id TEXT
                );

                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    slack_event_id TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    enterprise_id TEXT,
                    team_id TEXT,
                    context_team_id TEXT,
                    channel_id TEXT NOT NULL,
                    user_id TEXT,
                    event_ts TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    thread_ts TEXT,
                    file_id TEXT NOT NULL,
                    initial_comment TEXT,
                    file_name TEXT,
                    file_mime_type TEXT,
                    file_size_bytes INTEGER,
                    source_id TEXT,
                    source_record_path TEXT,
                    knowledge_note_paths_json TEXT NOT NULL DEFAULT '[]',
                    git_commit_hash TEXT,
                    slack_result_message_ts TEXT,
                    error_stage TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                """
            )

    def record_slack_event(
        self,
        event: SlackIngestionEvent,
        *,
        received_at: datetime | None = None,
    ) -> bool:
        """Persist a Slack event wrapper. Returns False for duplicate events."""

        self.initialize()
        timestamp = _timestamp(received_at)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO slack_events (
                    event_id,
                    event_type,
                    enterprise_id,
                    team_id,
                    context_team_id,
                    channel_id,
                    user_id,
                    event_ts,
                    message_ts,
                    thread_ts,
                    file_id,
                    raw_payload_json,
                    received_at,
                    duplicate_of_event_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.enterprise_id,
                    event.team_id,
                    event.context_team_id,
                    event.channel_id,
                    event.user_id,
                    event.event_ts,
                    event.message_ts,
                    event.thread_ts,
                    event.file_id,
                    json.dumps(event.raw_payload, sort_keys=True),
                    timestamp,
                ),
            )
            return cursor.rowcount == 1

    def enqueue_ingestion_job(
        self,
        event: SlackIngestionEvent,
        *,
        created_at: datetime | None = None,
    ) -> EnqueueJobResult:
        """Create or return the idempotent ingestion job for a Slack file event."""

        self.initialize()
        timestamp = _timestamp(created_at)
        job_id = _job_id(event.dedupe_key)
        with self._connection() as connection:
            existing_file_job = _find_existing_file_job(
                connection,
                enterprise_id=event.enterprise_id,
                team_id=event.team_id,
                channel_id=event.channel_id,
                file_id=event.file_id,
                statuses=(
                    IngestionJobStatus.QUEUED,
                    IngestionJobStatus.RUNNING,
                    IngestionJobStatus.SUCCEEDED,
                ),
            )
            if existing_file_job is not None:
                return EnqueueJobResult(
                    job=_job_from_row(existing_file_job),
                    created=False,
                )

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO ingestion_jobs (
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
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    IngestionJobStatus.QUEUED.value,
                    event.event_id,
                    event.dedupe_key,
                    event.enterprise_id,
                    event.team_id,
                    event.context_team_id,
                    event.channel_id,
                    event.user_id,
                    event.event_ts,
                    event.message_ts,
                    event.thread_ts,
                    event.file_id,
                    event.initial_comment,
                    timestamp,
                ),
            )
            row = connection.execute(
                _SELECT_JOB_SQL + " WHERE dedupe_key = ?",
                (event.dedupe_key,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to create or load Slack ingestion job")
        return EnqueueJobResult(job=_job_from_row(row), created=cursor.rowcount == 1)

    def claim_next_queued_job(
        self,
        *,
        started_at: datetime | None = None,
    ) -> IngestionJob | None:
        """Claim the oldest queued job for processing."""

        self.initialize()
        timestamp = _timestamp(started_at)
        with self._connection() as connection:
            while True:
                row = connection.execute(
                    _SELECT_JOB_SQL + " WHERE status = ? ORDER BY created_at LIMIT 1",
                    (IngestionJobStatus.QUEUED.value,),
                ).fetchone()
                if row is None:
                    return None

                job = _job_from_row(row)
                duplicate = _find_existing_file_job(
                    connection,
                    enterprise_id=job.enterprise_id,
                    team_id=job.team_id,
                    channel_id=job.channel_id,
                    file_id=job.file_id,
                    statuses=(
                        IngestionJobStatus.RUNNING,
                        IngestionJobStatus.SUCCEEDED,
                    ),
                    exclude_job_id=job.job_id,
                )
                if duplicate is not None:
                    _mark_job_ignored_as_duplicate(
                        connection,
                        job_id=job.job_id,
                        duplicate_job_id=str(duplicate["job_id"]),
                        finished_at=timestamp,
                    )
                    continue

                cursor = connection.execute(
                    """
                    UPDATE ingestion_jobs
                    SET status = ?, started_at = ?
                    WHERE job_id = ? AND status = ?
                    """,
                    (
                        IngestionJobStatus.RUNNING.value,
                        timestamp,
                        job.job_id,
                        IngestionJobStatus.QUEUED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                claimed = connection.execute(
                    _SELECT_JOB_SQL + " WHERE job_id = ?",
                    (job.job_id,),
                ).fetchone()
                break

        if claimed is None:
            raise RuntimeError(f"Claimed job disappeared: {job.job_id}")
        return _job_from_row(claimed)

    def update_job_file_info(
        self,
        job_id: str,
        *,
        file_name: str | None,
        file_mime_type: str | None,
        file_size_bytes: int | None,
    ) -> None:
        """Persist Slack file metadata discovered during job processing."""

        self.initialize()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET file_name = ?, file_mime_type = ?, file_size_bytes = ?
                WHERE job_id = ?
                """,
                (file_name, file_mime_type, file_size_bytes, job_id),
            )

    def mark_job_succeeded(
        self,
        job_id: str,
        *,
        source_id: str,
        source_record_path: str,
        knowledge_note_paths: tuple[str, ...],
        git_commit_hash: str | None,
        slack_result_message_ts: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        """Mark a job as successfully ingested."""

        self.initialize()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?,
                    source_id = ?,
                    source_record_path = ?,
                    knowledge_note_paths_json = ?,
                    git_commit_hash = ?,
                    slack_result_message_ts = ?,
                    error_stage = NULL,
                    error_message = NULL,
                    finished_at = ?
                WHERE job_id = ?
                """,
                (
                    IngestionJobStatus.SUCCEEDED.value,
                    source_id,
                    source_record_path,
                    json.dumps(list(knowledge_note_paths), sort_keys=True),
                    git_commit_hash,
                    slack_result_message_ts,
                    _timestamp(finished_at),
                    job_id,
                ),
            )

    def mark_job_failed(
        self,
        job_id: str,
        *,
        error_stage: str,
        error_message: str,
        slack_result_message_ts: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        """Mark a job as failed."""

        self.initialize()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?,
                    error_stage = ?,
                    error_message = ?,
                    slack_result_message_ts = ?,
                    finished_at = ?
                WHERE job_id = ?
                """,
                (
                    IngestionJobStatus.FAILED.value,
                    error_stage,
                    error_message,
                    slack_result_message_ts,
                    _timestamp(finished_at),
                    job_id,
                ),
            )

    def get_job(self, job_id: str) -> IngestionJob | None:
        """Return a job by ID."""

        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                _SELECT_JOB_SQL + " WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def list_jobs(self) -> tuple[IngestionJob, ...]:
        """Return all jobs ordered by creation time."""

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                _SELECT_JOB_SQL + " ORDER BY created_at, job_id",
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()


_SELECT_JOB_SQL = """
SELECT
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
    file_name,
    file_mime_type,
    file_size_bytes,
    source_id,
    source_record_path,
    knowledge_note_paths_json,
    git_commit_hash,
    slack_result_message_ts,
    error_stage,
    error_message,
    created_at,
    started_at,
    finished_at
FROM ingestion_jobs
"""


def _find_existing_file_job(
    connection: sqlite3.Connection,
    *,
    enterprise_id: str | None,
    team_id: str | None,
    channel_id: str,
    file_id: str,
    statuses: tuple[IngestionJobStatus, ...],
    exclude_job_id: str | None = None,
) -> sqlite3.Row | None:
    if not statuses:
        return None
    status_placeholders = ",".join("?" for _status in statuses)
    exclude_clause = "" if exclude_job_id is None else "AND job_id != ?"
    params: list[str] = [
        enterprise_id or "",
        team_id or "",
        channel_id,
        file_id,
        *(status.value for status in statuses),
    ]
    if exclude_job_id is not None:
        params.append(exclude_job_id)
    return cast(
        sqlite3.Row | None,
        connection.execute(
            _SELECT_JOB_SQL
            + f"""
            WHERE COALESCE(enterprise_id, '') = ?
              AND COALESCE(team_id, '') = ?
              AND channel_id = ?
              AND file_id = ?
              AND status IN ({status_placeholders})
              {exclude_clause}
            ORDER BY created_at, job_id
            LIMIT 1
            """,
            tuple(params),
        ).fetchone(),
    )


def _mark_job_ignored_as_duplicate(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    duplicate_job_id: str,
    finished_at: str,
) -> None:
    connection.execute(
        """
        UPDATE ingestion_jobs
        SET status = ?,
            error_stage = ?,
            error_message = ?,
            finished_at = ?
        WHERE job_id = ? AND status = ?
        """,
        (
            IngestionJobStatus.IGNORED.value,
            "duplicate_slack_file",
            f"Duplicate Slack file event for job {duplicate_job_id}",
            finished_at,
            job_id,
            IngestionJobStatus.QUEUED.value,
        ),
    )


def _job_from_row(row: sqlite3.Row) -> IngestionJob:
    knowledge_note_paths = json.loads(str(row["knowledge_note_paths_json"]))
    if not isinstance(knowledge_note_paths, list):
        raise ValueError("knowledge_note_paths_json must be a list")
    return IngestionJob(
        job_id=str(row["job_id"]),
        status=IngestionJobStatus(str(row["status"])),
        slack_event_id=str(row["slack_event_id"]),
        dedupe_key=str(row["dedupe_key"]),
        enterprise_id=_optional_str(row["enterprise_id"]),
        team_id=_optional_str(row["team_id"]),
        context_team_id=_optional_str(row["context_team_id"]),
        channel_id=str(row["channel_id"]),
        user_id=_optional_str(row["user_id"]),
        event_ts=str(row["event_ts"]),
        message_ts=str(row["message_ts"]),
        thread_ts=_optional_str(row["thread_ts"]),
        file_id=str(row["file_id"]),
        initial_comment=_optional_str(row["initial_comment"]),
        file_name=_optional_str(row["file_name"]),
        file_mime_type=_optional_str(row["file_mime_type"]),
        file_size_bytes=_optional_int(row["file_size_bytes"]),
        source_id=_optional_str(row["source_id"]),
        source_record_path=_optional_str(row["source_record_path"]),
        knowledge_note_paths=tuple(str(path) for path in knowledge_note_paths),
        git_commit_hash=_optional_str(row["git_commit_hash"]),
        slack_result_message_ts=_optional_str(row["slack_result_message_ts"]),
        error_stage=_optional_str(row["error_stage"]),
        error_message=_optional_str(row["error_message"]),
        created_at=str(row["created_at"]),
        started_at=_optional_str(row["started_at"]),
        finished_at=_optional_str(row["finished_at"]),
    )


def _timestamp(value: datetime | None) -> str:
    return format_datetime(value or datetime.now(UTC))


def _job_id(dedupe_key: str) -> str:
    digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()
    return f"job-{digest[:16]}"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(str(value))
