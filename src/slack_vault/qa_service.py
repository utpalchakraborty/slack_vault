"""Reusable local vault Q&A orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from slack_vault.ai import AITextProvider, AnthropicAIProvider, RetryingAITextProvider
from slack_vault.config import Settings
from slack_vault.qa import (
    AIObsidianSearchQueryPlanner,
    AnswerResult,
    AnthropicQuestionAnswerer,
    QuestionAnswerer,
    SearchQueryPlanner,
)
from slack_vault.retrieval import (
    DEFAULT_RETRIEVAL_LIMIT,
    ObsidianCliSearch,
    ObsidianSearchProvider,
    VaultIndex,
    build_answer_context,
    load_vault_index,
)

VaultIndexLoader = Callable[[Path], VaultIndex]


@dataclass(frozen=True)
class LocalQuestionAnsweringService:
    """Service that answers questions from the configured local Obsidian vault."""

    settings: Settings
    query_planner: SearchQueryPlanner
    answerer: QuestionAnswerer
    search_provider: ObsidianSearchProvider
    index_loader: VaultIndexLoader = load_vault_index

    def answer_question(
        self,
        question: str,
        *,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
    ) -> AnswerResult:
        """Plan vault searches, retrieve local context, and synthesize an answer."""

        search_plan = self.query_planner.plan(question)
        context = build_answer_context(
            self.index_loader(self.settings.obsidian_vault_path),
            question,
            search_provider=self.search_provider,
            search_queries=search_plan.queries,
            limit=limit,
        )
        if not context.items:
            return AnswerResult.no_evidence_result(context)
        return self.answerer.answer(context)


def build_local_question_answering_service(
    settings: Settings,
    *,
    ai_provider: AITextProvider | None = None,
) -> LocalQuestionAnsweringService:
    """Build the default local Q&A service from runtime settings."""

    if ai_provider is None:
        ai_provider = RetryingAITextProvider(
            AnthropicAIProvider.from_settings(settings),
            retry=settings.ai.retry,
        )
    vault_name = settings.obsidian_cli_vault_name or settings.obsidian_vault_path.name
    return LocalQuestionAnsweringService(
        settings=settings,
        query_planner=AIObsidianSearchQueryPlanner(ai_provider),
        answerer=AnthropicQuestionAnswerer(ai_provider),
        search_provider=ObsidianCliSearch(vault_name=vault_name),
    )


def answer_question_from_settings(
    settings: Settings,
    question: str,
    *,
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
) -> AnswerResult:
    """Answer one local vault question using settings-derived dependencies."""

    return build_local_question_answering_service(settings).answer_question(
        question,
        limit=limit,
    )
