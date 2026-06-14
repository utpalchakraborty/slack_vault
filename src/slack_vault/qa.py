"""Local vault question answering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from slack_vault.ai import AIPromptCacheConfig, AITextProvider, AITextRequest
from slack_vault.retrieval import AnswerContext, AnswerContextItem

DEFAULT_QA_MAX_OUTPUT_TOKENS = 4_096
NO_EVIDENCE_ANSWER = (
    "I could not find enough relevant vault context to answer this question."
)
QA_SYSTEM_PROMPT = """You answer questions by synthesizing Obsidian vault search hits.

Return a single JSON object with exactly this shape:
{
  "answer": "A concise answer with citation labels like [1].",
  "citation_ids": [1]
}

Rules:
- Use only the provided Obsidian vault search hits.
- Do not invent facts, dates, people, organizations, or conclusions.
- Cite every factual claim with the provided bracket labels.
- Set citation_ids to the citation_id values used in the answer.
- If the context is insufficient, say what is missing instead of guessing.
- Treat the AI step as synthesis over the vault, not as replacement storage.
"""


@dataclass(frozen=True)
class AnswerCitation:
    """A citation attached to a generated answer."""

    citation_id: int
    note_title: str
    note_path: Path
    source_ids: tuple[str, ...]
    source_record_paths: tuple[Path, ...]


@dataclass(frozen=True)
class AnswerResult:
    """Result of answering a local vault question."""

    question: str
    answer: str
    citations: tuple[AnswerCitation, ...]
    context: AnswerContext
    answerer_name: str
    no_evidence: bool = False
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def no_evidence_result(cls, context: AnswerContext) -> AnswerResult:
        """Create the deterministic no-evidence answer."""

        return cls(
            question=context.question,
            answer=NO_EVIDENCE_ANSWER,
            citations=(),
            context=context,
            answerer_name="local",
            no_evidence=True,
        )


class QuestionAnswerer(Protocol):
    """Interface for local vault answer generators."""

    @property
    def name(self) -> str:
        """Return the answerer identifier."""
        ...

    def answer(self, context: AnswerContext) -> AnswerResult:
        """Answer a question from retrieved context."""
        ...


@dataclass(frozen=True)
class AnthropicQuestionAnswerer:
    """Anthropic-backed answer generation over local vault context."""

    provider: AITextProvider
    max_output_tokens: int = DEFAULT_QA_MAX_OUTPUT_TOKENS
    prompt_cache: AIPromptCacheConfig | None = AIPromptCacheConfig(
        automatic=False,
        cache_system_prompt=True,
    )
    name: str = "anthropic"

    def answer(self, context: AnswerContext) -> AnswerResult:
        """Generate an answer using retrieved context."""

        if not context.items:
            return AnswerResult.no_evidence_result(context)

        response = self.provider.complete_text(
            AITextRequest(
                system_prompt=QA_SYSTEM_PROMPT,
                user_prompt=_answer_user_prompt(context),
                max_output_tokens=self.max_output_tokens,
                temperature=0,
                prompt_cache=self.prompt_cache,
            )
        )
        candidate = _parse_answer_candidate(response.text)
        return AnswerResult(
            question=context.question,
            answer=candidate.answer,
            citations=_citations_for_ids(context, candidate.citation_ids),
            context=context,
            answerer_name=self.name,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_creation_input_tokens=response.cache_creation_input_tokens,
            cache_read_input_tokens=response.cache_read_input_tokens,
        )


def render_answer_result(result: AnswerResult) -> str:
    """Render an answer result for CLI output."""

    lines = ["Answer:", result.answer, ""]
    if result.citations:
        lines.append("Citations:")
        lines.extend(
            f"- {render_answer_citation(citation)}" for citation in result.citations
        )
    else:
        lines.append("Citations: none")
    return "\n".join(lines)


def render_answer_citation(citation: AnswerCitation) -> str:
    """Render one Markdown-style answer citation."""

    parts = [
        f"[{citation.citation_id}] "
        f"{_obsidian_link(citation.note_path, citation.note_title)}"
    ]
    if citation.source_ids:
        parts.append(
            "sources: "
            + ", ".join(f"`{source_id}`" for source_id in citation.source_ids)
        )
    if citation.source_record_paths:
        parts.append(
            "records: "
            + ", ".join(
                _obsidian_link(path, path.stem) for path in citation.source_record_paths
            )
        )
    return "; ".join(parts)


@dataclass(frozen=True)
class _AnswerCandidate:
    answer: str
    citation_ids: tuple[int, ...]


def _answer_user_prompt(context: AnswerContext) -> str:
    context_payload = [
        {
            "citation_id": item.citation_id,
            "note_title": item.note_title,
            "note_path": item.note_path.as_posix(),
            "obsidian_path": item.note_path.with_suffix("").as_posix(),
            "source_ids": list(item.source_ids),
            "source_record_paths": [
                path.as_posix() for path in item.source_record_paths
            ],
            "tags": list(item.tags),
            "wikilinks": list(item.wikilinks),
            "matched_terms": list(item.matched_terms),
            "excerpt": item.excerpt,
        }
        for item in context.items
    ]
    return "\n".join(
        [
            f"Question: {context.question}",
            f"Obsidian search query: {context.search_query}",
            "",
            "Obsidian vault search hits JSON:",
            json.dumps(context_payload, indent=2, sort_keys=True),
        ]
    )


def _parse_answer_candidate(response_text: str) -> _AnswerCandidate:
    parsed: object = json.loads(_strip_json_fence(response_text))
    if not isinstance(parsed, dict):
        raise ValueError("Answer response must be a JSON object")

    answer = _required_string(parsed, "answer")
    citation_ids = _required_citation_ids(parsed)
    return _AnswerCandidate(answer=answer, citation_ids=citation_ids)


def _required_string(data: dict[object, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Answer response must include "{key}"')
    return value.strip()


def _required_citation_ids(data: dict[object, object]) -> tuple[int, ...]:
    value = data.get("citation_ids")
    if not isinstance(value, list):
        raise ValueError('Answer response field "citation_ids" must be a list')

    citation_ids: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise ValueError("Answer citation_ids must contain only integers")
        citation_ids.append(item)
    if not citation_ids:
        raise ValueError("Answer response must include at least one citation_id")
    return tuple(citation_ids)


def _citations_for_ids(
    context: AnswerContext,
    citation_ids: tuple[int, ...],
) -> tuple[AnswerCitation, ...]:
    item_by_id = {item.citation_id: item for item in context.items}
    unknown_ids = tuple(
        citation_id for citation_id in citation_ids if citation_id not in item_by_id
    )
    if unknown_ids:
        raise ValueError(f"Answer cited unknown context IDs: {unknown_ids}")

    citations: list[AnswerCitation] = []
    seen_ids: set[int] = set()
    for citation_id in citation_ids:
        if citation_id in seen_ids:
            continue
        seen_ids.add(citation_id)
        citations.append(_citation_from_context_item(item_by_id[citation_id]))
    return tuple(citations)


def _citation_from_context_item(item: AnswerContextItem) -> AnswerCitation:
    return AnswerCitation(
        citation_id=item.citation_id,
        note_title=item.note_title,
        note_path=item.note_path,
        source_ids=item.source_ids,
        source_record_paths=item.source_record_paths,
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped

    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _obsidian_link(relative_path: Path, title: str) -> str:
    target = relative_path.with_suffix("").as_posix()
    return f"[[{_clean_wikilink_text(target)}|{_clean_wikilink_text(title)}]]"


def _clean_wikilink_text(value: str) -> str:
    return value.replace("|", "-").replace("]", "")
