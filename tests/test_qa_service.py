from __future__ import annotations

from pathlib import Path

import pytest

from slack_vault.ai import AITextRequest, AITextResponse
from slack_vault.config import Settings
from slack_vault.qa import (
    NO_EVIDENCE_ANSWER,
    AnswerCitation,
    AnswerResult,
    SearchQueryPlan,
)
from slack_vault.qa_service import (
    LocalQuestionAnsweringService,
    answer_question_from_settings,
)
from slack_vault.retrieval import AnswerContext, ObsidianSearchProvider


def test_local_question_answering_service_returns_no_evidence_without_answerer(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    search = _FakeSearch(())
    service = LocalQuestionAnsweringService(
        settings=settings,
        query_planner=_FakePlanner(("unknown topic",)),
        answerer=_FailingAnswerer(),
        search_provider=search,
    )

    result = service.answer_question("What is unknown?", limit=2)

    assert result.no_evidence is True
    assert result.answer == NO_EVIDENCE_ANSWER
    assert result.citations == ()
    assert result.context.search_query == "unknown topic"
    assert search.calls == [("unknown topic", 2)]


def test_local_question_answering_service_returns_mocked_ai_answer(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_qa_fixture(settings.obsidian_vault_path)
    search = _FakeSearch((Path("10 Knowledge/project-alpha-plan.md"),))
    answerer = _FakeAnswerer()
    service = LocalQuestionAnsweringService(
        settings=settings,
        query_planner=_FakePlanner(("Project Alpha", "local-first ingest")),
        answerer=answerer,
        search_provider=search,
    )

    result = service.answer_question("What does Project Alpha need?")

    assert result.answer == "Project Alpha needs local-first ingest [1]."
    assert result.citations == (
        AnswerCitation(
            citation_id=1,
            note_title="Project Alpha Plan",
            note_path=Path("10 Knowledge/project-alpha-plan.md"),
            source_ids=("source-alpha",),
            source_record_paths=(Path("20 Sources/sources/source-alpha.md"),),
        ),
    )
    assert answerer.context is not None
    assert answerer.context.search_query == "Project Alpha, local-first ingest"
    assert search.calls == [("Project Alpha", 5), ("local-first ingest", 5)]


def test_answer_question_from_settings_propagates_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tmp_path,
        extra_env={
            "ANTHROPIC_API_KEY": "test-key",
            "SLACK_VAULT_AI_RETRY_MAX_ATTEMPTS": "1",
        },
    )
    monkeypatch.setattr(
        "slack_vault.qa_service.AnthropicAIProvider.from_settings",
        lambda settings: _FailingProvider(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        answer_question_from_settings(settings, "What fails?")


def _settings(
    tmp_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> Settings:
    env = {
        "SLACK_VAULT_OBSIDIAN_PATH": str(tmp_path / "vault"),
        **(extra_env or {}),
    }
    return Settings.from_env(env)


class _FakePlanner:
    name = "fake-planner"

    def __init__(self, queries: tuple[str, ...]) -> None:
        self.queries = queries
        self.questions: list[str] = []

    def plan(self, question: str) -> SearchQueryPlan:
        self.questions.append(question)
        return SearchQueryPlan(
            question=question,
            queries=self.queries,
            planner_name=self.name,
        )


class _FakeAnswerer:
    name = "fake-answerer"

    def __init__(self) -> None:
        self.context: AnswerContext | None = None

    def answer(self, context: AnswerContext) -> AnswerResult:
        self.context = context
        citation = context.items[0]
        return AnswerResult(
            question=context.question,
            answer="Project Alpha needs local-first ingest [1].",
            citations=(
                AnswerCitation(
                    citation_id=citation.citation_id,
                    note_title=citation.note_title,
                    note_path=citation.note_path,
                    source_ids=citation.source_ids,
                    source_record_paths=citation.source_record_paths,
                ),
            ),
            context=context,
            answerer_name=self.name,
        )


class _FailingAnswerer:
    name = "failing-answerer"

    def answer(self, context: AnswerContext) -> AnswerResult:
        raise AssertionError("answerer should not be called")


class _FakeSearch(ObsidianSearchProvider):
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int) -> tuple[Path, ...]:
        self.calls.append((query, limit))
        return self.paths[:limit]


class _FailingProvider:
    def complete_text(self, request: AITextRequest) -> AITextResponse:
        raise RuntimeError("provider unavailable")


def _write_qa_fixture(vault_path: Path) -> None:
    note_path = vault_path / "10 Knowledge/project-alpha-plan.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "\n".join(
            [
                "---",
                'title: "Project Alpha Plan"',
                'type: "knowledge_note"',
                'note_id: "knowledge-project-alpha-plan"',
                'source_ids: ["source-alpha"]',
                'topics: ["Project Alpha"]',
                'taxonomy: ["projects"]',
                "---",
                "",
                "# Project Alpha Plan",
                "",
                "Project Alpha needs local-first ingest.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    source_path = vault_path / "20 Sources/sources/source-alpha.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "\n".join(
            [
                "---",
                'title: "Alpha Plan.docx"',
                'type: "source_record"',
                'source_id: "source-alpha"',
                'original_filename: "Alpha Plan.docx"',
                "---",
                "",
                "# Alpha Plan.docx",
                "",
            ]
        ),
        encoding="utf-8",
    )
