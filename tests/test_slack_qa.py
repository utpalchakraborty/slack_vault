from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slack_vault.config import Settings
from slack_vault.ops_state import QAJobStatus, SQLiteOperationalState
from slack_vault.qa import NO_EVIDENCE_ANSWER, AnswerCitation, AnswerResult
from slack_vault.retrieval import AnswerContext
from slack_vault.slack_qa import (
    CHECKING_VAULT_MESSAGE,
    SlackQuestionAnsweringService,
    render_slack_answer_result,
)


def test_slack_qa_service_enqueues_dm_once_and_posts_checking_message(
    tmp_path: Path,
) -> None:
    service, client, _answerer = _service(tmp_path)
    payload = _message_im_payload()

    first = service.handle_event_payload(payload)
    second = service.handle_event_payload(payload)

    assert len(first) == 1
    assert first[0].created is True
    assert len(second) == 1
    assert second[0].created is False
    assert len(service.state.list_qa_jobs()) == 1
    assert client.messages == [
        {
            "channel": "D123",
            "text": CHECKING_VAULT_MESSAGE,
        }
    ]
    stored = service.state.get_qa_job(first[0].job.job_id)
    assert stored is not None
    assert stored.slack_initial_message_ts == "1718300001.000100"


def test_slack_qa_service_processes_job_and_posts_answer(
    tmp_path: Path,
) -> None:
    service, client, answerer = _service(tmp_path)
    service.handle_event_payload(_message_im_payload())

    job = service.process_next_job()

    assert job is not None
    assert job.status is QAJobStatus.SUCCEEDED
    assert answerer.calls == [("What does Project Alpha need?", 5)]
    assert job.search_query == "Project Alpha"
    assert job.answer_text == "Project Alpha needs local-first ingest [1]."
    citations = json.loads(job.citations_json)
    assert citations == [
        {
            "citation_id": 1,
            "note_path": "10 Knowledge/project-alpha-plan.md",
            "note_title": "Project Alpha Plan",
            "source_ids": ["source-alpha"],
            "source_record_paths": ["20 Sources/sources/source-alpha.md"],
        }
    ]
    assert client.messages[-1] == {
        "channel": "D123",
        "text": (
            "Project Alpha needs local-first ingest [1].\n\n"
            "Evidence:\n"
            "[1] *Project Alpha Plan* `10 Knowledge/project-alpha-plan.md` "
            "Source: `source-alpha` Records: `20 Sources/sources/source-alpha.md`"
        ),
    }


def test_slack_qa_service_marks_failed_job_and_posts_failure(
    tmp_path: Path,
) -> None:
    service, client, answerer = _service(tmp_path)
    answerer.fail = True
    service.handle_event_payload(_message_im_payload())

    job = service.process_next_job()

    assert job is not None
    assert job.status is QAJobStatus.FAILED
    assert job.error_stage == "slack_qa"
    assert job.error_message == "rate limited"
    assert client.messages[-1]["channel"] == "D123"
    assert "rate limited" in str(client.messages[-1]["text"])


def test_render_slack_answer_result_omits_evidence_for_no_evidence() -> None:
    context = AnswerContext(
        question="What is unknown?",
        search_query="unknown",
        items=(),
    )
    result = AnswerResult(
        question=context.question,
        answer=NO_EVIDENCE_ANSWER,
        citations=(),
        context=context,
        answerer_name="local",
        no_evidence=True,
    )

    rendered = render_slack_answer_result(result)

    assert rendered == NO_EVIDENCE_ANSWER
    assert "Evidence:" not in rendered


def _service(
    tmp_path: Path,
) -> tuple[SlackQuestionAnsweringService, _FakeSlackClient, _FakeAnswerQuestion]:
    settings = Settings.from_env(
        {
            "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
            "SLACK_VAULT_OPERATIONAL_DB_PATH": str(tmp_path / "state.sqlite3"),
        }
    )
    client = _FakeSlackClient()
    answerer = _FakeAnswerQuestion()
    service = SlackQuestionAnsweringService(
        settings=settings,
        state=SQLiteOperationalState(settings.operational.db_path),
        slack_client=client,
        answer_question=answerer,
    )
    return service, client, answerer


def _message_im_payload() -> dict[str, object]:
    return {
        "type": "event_callback",
        "team_id": "T123",
        "context_team_id": "TCTX",
        "context_enterprise_id": "E123",
        "event_id": "Ev-qa",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "user": "W123",
            "text": "What does Project Alpha need?",
            "ts": "1718300000.000100",
            "event_ts": "1718300000.000200",
        },
    }


class _FakeSlackClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        self.messages.append(dict(kwargs))
        return {"ok": True, "ts": f"171830000{len(self.messages)}.000100"}


class _FakeAnswerQuestion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = False

    def __call__(
        self,
        settings: Settings,
        question: str,
        *,
        limit: int,
    ) -> AnswerResult:
        if self.fail:
            raise RuntimeError("rate limited")
        self.calls.append((question, limit))
        return _answer_result(question)


def _answer_result(question: str) -> AnswerResult:
    return AnswerResult(
        question=question,
        answer="Project Alpha needs local-first ingest [1].",
        citations=(
            AnswerCitation(
                citation_id=1,
                note_title="Project Alpha Plan",
                note_path=Path("10 Knowledge/project-alpha-plan.md"),
                source_ids=("source-alpha",),
                source_record_paths=(Path("20 Sources/sources/source-alpha.md"),),
            ),
        ),
        context=AnswerContext(
            question=question,
            search_query="Project Alpha",
            items=(),
        ),
        answerer_name="fake",
    )
