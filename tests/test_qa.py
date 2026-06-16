from __future__ import annotations

import json
from pathlib import Path

import pytest

from slack_vault.ai import AITextRequest, AITextResponse
from slack_vault.qa import (
    NO_EVIDENCE_ANSWER,
    QA_SEARCH_PLAN_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    AIObsidianSearchQueryPlanner,
    AnswerCitation,
    AnswerResult,
    AnthropicQuestionAnswerer,
    render_answer_result,
)
from slack_vault.retrieval import AnswerContext, AnswerContextItem


def test_anthropic_question_answerer_assembles_prompt_and_parses_answer() -> None:
    provider = _FakeTextProvider(
        "```json\n"
        + json.dumps(
            {
                "answer": "Project Alpha needs local-first ingest [1].",
                "citation_ids": [1],
            }
        )
        + "\n```"
    )

    result = AnthropicQuestionAnswerer(provider).answer(_answer_context())

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
    assert result.model == "fake-model"
    assert result.input_tokens == 12
    assert result.cache_creation_input_tokens == 4

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.system_prompt == QA_SYSTEM_PROMPT
    assert "Question: What does Project Alpha need?" in request.user_prompt
    assert "Obsidian search queries: project alpha" in request.user_prompt
    assert "Obsidian vault search hits JSON" in request.user_prompt
    assert '"citation_id": 1' in request.user_prompt
    assert '"note_path": "10 Knowledge/project-alpha-plan.md"' in request.user_prompt
    assert '"obsidian_path": "10 Knowledge/project-alpha-plan"' in request.user_prompt
    assert '"tags": [' in request.user_prompt
    assert '"wikilinks": [' in request.user_prompt
    assert request.temperature == 0


def test_ai_search_query_planner_parses_search_queries() -> None:
    provider = _FakeTextProvider(
        json.dumps(
            {
                "queries": [
                    "NJ company filings",
                    "New Jersey company filings",
                    "Garden State Meridian Services",
                ]
            }
        )
    )

    result = AIObsidianSearchQueryPlanner(provider).plan(
        "What info do you have on NJ company filings?"
    )

    assert result.queries == (
        "NJ company filings",
        "New Jersey company filings",
        "Garden State Meridian Services",
    )
    assert result.model == "fake-model"
    request = provider.requests[0]
    assert request.system_prompt == QA_SEARCH_PLAN_SYSTEM_PROMPT
    assert request.user_prompt == (
        "Question: What info do you have on NJ company filings?"
    )
    assert request.temperature == 0


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ([], "Search query response must be a JSON object"),
        ({}, 'Search query response field "queries" must be a list'),
        ({"queries": [""]}, "must contain non-empty strings"),
        ({"queries": [1]}, "must contain non-empty strings"),
    ],
)
def test_ai_search_query_planner_rejects_invalid_query_shapes(
    payload: object,
    expected_error: str,
) -> None:
    provider = _FakeTextProvider(json.dumps(payload))

    with pytest.raises(ValueError, match=expected_error):
        AIObsidianSearchQueryPlanner(provider).plan("What should I search?")


def test_question_answerer_returns_no_evidence_without_calling_provider() -> None:
    context = AnswerContext(question="Unknown?", search_query="unknown", items=())

    result = AnthropicQuestionAnswerer(_FailingTextProvider()).answer(context)

    assert result.no_evidence is True
    assert result.answer == NO_EVIDENCE_ANSWER
    assert result.citations == ()


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ([], "Answer response must be a JSON object"),
        ({"citation_ids": [1]}, 'Answer response must include "answer"'),
        (
            {"answer": "ok", "citation_ids": "1"},
            'Answer response field "citation_ids" must be a list',
        ),
        (
            {"answer": "ok", "citation_ids": []},
            "Answer response must include at least one citation_id",
        ),
        (
            {"answer": "ok", "citation_ids": [True]},
            "Answer citation_ids must contain only integers",
        ),
    ],
)
def test_question_answerer_rejects_invalid_answer_shapes(
    payload: object,
    expected_error: str,
) -> None:
    provider = _FakeTextProvider(json.dumps(payload))

    with pytest.raises(ValueError, match=expected_error):
        AnthropicQuestionAnswerer(provider).answer(_answer_context())


def test_question_answerer_rejects_unknown_citation_id() -> None:
    provider = _FakeTextProvider(
        json.dumps({"answer": "Unknown citation [2].", "citation_ids": [2]})
    )

    with pytest.raises(ValueError, match=r"Answer cited unknown context IDs: \(2,\)"):
        AnthropicQuestionAnswerer(provider).answer(_answer_context())


def test_render_answer_result_outputs_markdown_citations() -> None:
    context = _answer_context()
    result = AnswerResult(
        question=context.question,
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
        context=context,
        answerer_name="fake",
    )

    rendered = render_answer_result(result)

    assert "Answer:\nProject Alpha needs local-first ingest [1]." in rendered
    assert "[[10 Knowledge/project-alpha-plan|Project Alpha Plan]]" in rendered
    assert "`source-alpha`" in rendered
    assert "[[20 Sources/sources/source-alpha|source-alpha]]" in rendered


class _FakeTextProvider:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.requests: list[AITextRequest] = []

    def complete_text(self, request: AITextRequest) -> AITextResponse:
        self.requests.append(request)
        return AITextResponse(
            text=self.response_text,
            model="fake-model",
            stop_reason="end_turn",
            input_tokens=12,
            output_tokens=8,
            cache_creation_input_tokens=4,
            cache_read_input_tokens=2,
        )


class _FailingTextProvider:
    def complete_text(self, request: AITextRequest) -> AITextResponse:
        raise AssertionError("provider should not be called")


def _answer_context() -> AnswerContext:
    return AnswerContext(
        question="What does Project Alpha need?",
        search_query="project alpha",
        items=(
            AnswerContextItem(
                citation_id=1,
                note_title="Project Alpha Plan",
                note_path=Path("10 Knowledge/project-alpha-plan.md"),
                source_ids=("source-alpha",),
                source_record_paths=(Path("20 Sources/sources/source-alpha.md"),),
                tags=("generated",),
                wikilinks=("Alpha",),
                excerpt="Project Alpha needs local-first ingest.",
                score=12,
                matched_terms=("project", "alpha"),
            ),
        ),
    )
