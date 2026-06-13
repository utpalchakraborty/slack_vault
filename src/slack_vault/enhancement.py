"""AI enhancement of deterministic extracted evidence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from slack_vault.ai import AIPromptCacheConfig, AITextProvider, AITextRequest
from slack_vault.archive import ArchivedSourceRef
from slack_vault.extraction import (
    EvidenceBlock,
    EvidenceLocation,
    ExtractionResult,
    ExtractionStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_ENHANCEMENT_MAX_OUTPUT_TOKENS = 2_048
ENHANCEMENT_SYSTEM_PROMPT = """You enhance source-grounded evidence for Slack Vault.

Return a JSON object with exactly this shape:
{"enhanced_text": "..."}

Rules:
- Preserve the original meaning and source facts.
- Do not invent facts, names, dates, numbers, or conclusions.
- Keep useful source detail; do not replace the evidence with only a summary.
- Clean obvious extraction noise, repair broken spacing, and use readable
  Markdown structure when it helps.
- Preserve table-like information as Markdown tables or compact lists when the
  structure is clear from the extracted text.
"""


class EnhancementStatus(StrEnum):
    """AI enhancement status values stored in source records."""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class EnhancedEvidenceBlock:
    """An AI-enhanced evidence block that preserves its deterministic source."""

    sequence: int
    source_sequence: int
    text: str
    location: EvidenceLocation


@dataclass(frozen=True)
class EnhancementResult:
    """Result of optional AI evidence enhancement."""

    status: EnhancementStatus
    enhancer_name: str
    enhanced_evidence: tuple[EnhancedEvidenceBlock, ...] = ()
    error_message: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def completed(
        cls,
        *,
        enhancer_name: str,
        enhanced_evidence: tuple[EnhancedEvidenceBlock, ...],
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
    ) -> EnhancementResult:
        """Create a successful enhancement result."""

        return cls(
            status=EnhancementStatus.COMPLETED,
            enhancer_name=enhancer_name,
            enhanced_evidence=enhanced_evidence,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )

    @classmethod
    def failed(cls, *, enhancer_name: str, error_message: str) -> EnhancementResult:
        """Create a failed enhancement result."""

        return cls(
            status=EnhancementStatus.FAILED,
            enhancer_name=enhancer_name,
            error_message=error_message,
        )

    @classmethod
    def skipped(cls, *, enhancer_name: str, reason: str) -> EnhancementResult:
        """Create a skipped enhancement result."""

        return cls(
            status=EnhancementStatus.SKIPPED,
            enhancer_name=enhancer_name,
            error_message=reason,
        )


class EvidenceEnhancer(Protocol):
    """Interface for optional evidence enhancement."""

    @property
    def name(self) -> str:
        """Return the enhancer identifier stored in source records."""
        ...

    def enhance(
        self,
        ref: ArchivedSourceRef,
        extraction_result: ExtractionResult,
    ) -> EnhancementResult:
        """Enhance deterministic evidence while preserving source anchors."""


@dataclass(frozen=True)
class AnthropicEvidenceEnhancer:
    """Anthropic-backed evidence enhancer using the text provider harness."""

    provider: AITextProvider
    max_output_tokens: int = DEFAULT_ENHANCEMENT_MAX_OUTPUT_TOKENS
    prompt_cache: AIPromptCacheConfig | None = AIPromptCacheConfig(
        automatic=False,
        cache_system_prompt=True,
    )
    name: str = "anthropic"

    def enhance(
        self,
        ref: ArchivedSourceRef,
        extraction_result: ExtractionResult,
    ) -> EnhancementResult:
        """Enhance each deterministic evidence block with AI."""

        logger.info(
            "Evidence enhancement started enhancer=%s filename=%s "
            "extraction_status=%s evidence_blocks=%s",
            self.name,
            ref.original_filename,
            extraction_result.status.value,
            len(extraction_result.evidence),
        )
        if extraction_result.status is not ExtractionStatus.COMPLETED:
            logger.info(
                "Evidence enhancement skipped enhancer=%s reason=extraction_status_%s",
                self.name,
                extraction_result.status.value,
            )
            return EnhancementResult.skipped(
                enhancer_name=self.name,
                reason=f"Extraction status is {extraction_result.status.value}.",
            )
        if not extraction_result.evidence:
            logger.info(
                "Evidence enhancement skipped enhancer=%s reason=no_evidence",
                self.name,
            )
            return EnhancementResult.skipped(
                enhancer_name=self.name,
                reason="No deterministic evidence blocks to enhance.",
            )

        enhanced_blocks: list[EnhancedEvidenceBlock] = []
        input_tokens = 0
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
        model: str | None = None

        try:
            for block in extraction_result.evidence:
                logger.debug(
                    "Enhancing evidence block enhancer=%s sequence=%s location=%s",
                    self.name,
                    block.sequence,
                    block.location.label(),
                )
                response = self.provider.complete_text(
                    AITextRequest(
                        system_prompt=ENHANCEMENT_SYSTEM_PROMPT,
                        user_prompt=_enhancement_user_prompt(ref, block),
                        max_output_tokens=self.max_output_tokens,
                        temperature=0,
                        prompt_cache=self.prompt_cache,
                    )
                )
                enhanced_blocks.append(
                    EnhancedEvidenceBlock(
                        sequence=len(enhanced_blocks) + 1,
                        source_sequence=block.sequence,
                        text=_parse_enhanced_text(response.text),
                        location=block.location,
                    )
                )
                input_tokens += response.input_tokens
                output_tokens += response.output_tokens
                cache_creation_input_tokens += response.cache_creation_input_tokens
                cache_read_input_tokens += response.cache_read_input_tokens
                model = response.model
        except Exception as exc:
            logger.exception("Evidence enhancement failed enhancer=%s", self.name)
            return EnhancementResult.failed(
                enhancer_name=self.name,
                error_message=str(exc),
            )

        logger.info(
            "Evidence enhancement completed enhancer=%s enhanced_blocks=%s model=%s "
            "input_tokens=%s output_tokens=%s cache_creation_input_tokens=%s "
            "cache_read_input_tokens=%s",
            self.name,
            len(enhanced_blocks),
            model,
            input_tokens,
            output_tokens,
            cache_creation_input_tokens,
            cache_read_input_tokens,
        )
        return EnhancementResult.completed(
            enhancer_name=self.name,
            enhanced_evidence=tuple(enhanced_blocks),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )


def _enhancement_user_prompt(ref: ArchivedSourceRef, block: EvidenceBlock) -> str:
    return "\n".join(
        [
            f"Source filename: {ref.original_filename}",
            f"MIME type: {ref.mime_type}",
            f"Evidence sequence: {block.sequence}",
            f"Source location: {block.location.label()}",
            "",
            "Extracted evidence:",
            "```text",
            block.text,
            "```",
        ]
    )


def _parse_enhanced_text(response_text: str) -> str:
    candidate = _strip_json_fence(response_text)
    parsed: object = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("Enhancement response must be a JSON object")

    enhanced_text = parsed.get("enhanced_text")
    if not isinstance(enhanced_text, str) or not enhanced_text.strip():
        raise ValueError('Enhancement response must include "enhanced_text"')
    return enhanced_text.strip()


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
