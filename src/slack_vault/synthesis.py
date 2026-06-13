"""AI classification and Obsidian knowledge-note synthesis."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from slack_vault.ai import AIPromptCacheConfig, AITextProvider, AITextRequest
from slack_vault.archive import ArchivedSourceRef, format_datetime
from slack_vault.enhancement import EnhancementResult, EnhancementStatus
from slack_vault.extraction import ExtractionResult, ExtractionStatus

logger = logging.getLogger(__name__)

KNOWLEDGE_NOTES_DIRECTORY = Path("10 Knowledge")
DEFAULT_SYNTHESIS_MAX_OUTPUT_TOKENS = 8_192
SYNTHESIS_MATCH_CONFIDENCE_THRESHOLD = 0.75
KNOWLEDGE_NOTE_FENCE = "````"
SYNTHESIS_SYSTEM_PROMPT = """You synthesize source-grounded Slack Vault notes.

Return a single JSON object with exactly this shape:
{
  "title": "Readable knowledge note title",
  "document_type": "brief document type label",
  "topics": ["topic"],
  "taxonomy": ["category"],
  "summary": "Short factual summary",
  "body": "Readable Markdown body for the knowledge note",
  "matched_note_path": "10 Knowledge/existing-note.md or null",
  "match_confidence": 0.0,
  "citations": [
    {
      "source_sequence": 1,
      "location": "source location label",
      "quote": "short cited fact"
    }
  ],
  "uncertainty": "optional uncertainty note or null"
}

Rules:
- Use only the provided source evidence and existing note excerpts.
- Do not invent facts, dates, people, organizations, or conclusions.
- Prefer updating an existing note only when it is clearly about the same topic.
- Set match_confidence from 0 to 1 for the matched_note_path decision.
- Keep body as Markdown without frontmatter or an H1 title.
- Include citations for the important claims in the body.
"""


class SynthesisStatus(StrEnum):
    """Knowledge synthesis status values."""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SourceClassification:
    """AI classification and taxonomy metadata for a source."""

    document_type: str
    topics: tuple[str, ...]
    taxonomy: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class KnowledgeCitation:
    """A source-grounded citation emitted by synthesis."""

    source_sequence: int
    location: str
    quote: str | None = None


@dataclass(frozen=True)
class ExistingKnowledgeNote:
    """A note already present in the knowledge vault."""

    title: str
    path: Path
    relative_path: Path
    note_id: str | None
    source_ids: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class KnowledgeNoteWriteResult:
    """Result of creating or updating a knowledge note."""

    note_id: str
    title: str
    path: Path
    relative_path: Path
    created: bool


@dataclass(frozen=True)
class KnowledgeSynthesisResult:
    """Result of AI classification and knowledge-note synthesis."""

    status: SynthesisStatus
    synthesizer_name: str
    source_id: str
    note: KnowledgeNoteWriteResult | None = None
    classification: SourceClassification | None = None
    citations: tuple[KnowledgeCitation, ...] = ()
    matched_note_path: str | None = None
    match_confidence: float | None = None
    uncertainty: str | None = None
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
        synthesizer_name: str,
        source_id: str,
        note: KnowledgeNoteWriteResult,
        classification: SourceClassification,
        citations: tuple[KnowledgeCitation, ...],
        matched_note_path: str | None,
        match_confidence: float,
        uncertainty: str | None,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
    ) -> KnowledgeSynthesisResult:
        """Create a successful synthesis result."""

        return cls(
            status=SynthesisStatus.COMPLETED,
            synthesizer_name=synthesizer_name,
            source_id=source_id,
            note=note,
            classification=classification,
            citations=citations,
            matched_note_path=matched_note_path,
            match_confidence=match_confidence,
            uncertainty=uncertainty,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )

    @classmethod
    def failed(
        cls,
        *,
        synthesizer_name: str,
        source_id: str,
        error_message: str,
    ) -> KnowledgeSynthesisResult:
        """Create a failed synthesis result."""

        return cls(
            status=SynthesisStatus.FAILED,
            synthesizer_name=synthesizer_name,
            source_id=source_id,
            error_message=error_message,
        )

    @classmethod
    def skipped(
        cls,
        *,
        synthesizer_name: str,
        source_id: str,
        reason: str,
    ) -> KnowledgeSynthesisResult:
        """Create a skipped synthesis result."""

        return cls(
            status=SynthesisStatus.SKIPPED,
            synthesizer_name=synthesizer_name,
            source_id=source_id,
            error_message=reason,
        )


class KnowledgeSynthesizer(Protocol):
    """Interface for source-to-knowledge-note synthesis."""

    @property
    def name(self) -> str:
        """Return the synthesizer identifier."""
        ...

    def synthesize(
        self,
        vault_path: Path,
        ref: ArchivedSourceRef,
        source_id: str,
        extraction_result: ExtractionResult,
        enhancement_result: EnhancementResult | None = None,
        *,
        now: datetime | None = None,
    ) -> KnowledgeSynthesisResult:
        """Create or update knowledge notes from source evidence."""


@dataclass(frozen=True)
class AnthropicKnowledgeSynthesizer:
    """Anthropic-backed source classification and knowledge-note synthesis."""

    provider: AITextProvider
    max_output_tokens: int = DEFAULT_SYNTHESIS_MAX_OUTPUT_TOKENS
    match_confidence_threshold: float = SYNTHESIS_MATCH_CONFIDENCE_THRESHOLD
    prompt_cache: AIPromptCacheConfig | None = AIPromptCacheConfig(
        automatic=False,
        cache_system_prompt=True,
    )
    name: str = "anthropic"

    def synthesize(
        self,
        vault_path: Path,
        ref: ArchivedSourceRef,
        source_id: str,
        extraction_result: ExtractionResult,
        enhancement_result: EnhancementResult | None = None,
        *,
        now: datetime | None = None,
    ) -> KnowledgeSynthesisResult:
        """Classify a source and create or update a knowledge note."""

        evidence = _synthesis_evidence(extraction_result, enhancement_result)
        logger.info(
            "Knowledge synthesis started synthesizer=%s source_id=%s filename=%s "
            "evidence_blocks=%s",
            self.name,
            source_id,
            ref.original_filename,
            len(evidence),
        )
        if not evidence:
            logger.info(
                "Knowledge synthesis skipped synthesizer=%s source_id=%s "
                "reason=no_evidence",
                self.name,
                source_id,
            )
            return KnowledgeSynthesisResult.skipped(
                synthesizer_name=self.name,
                source_id=source_id,
                reason="No extracted or enhanced evidence is available for synthesis.",
            )

        existing_notes = read_existing_knowledge_notes(vault_path)
        logger.info(
            "Existing knowledge notes loaded synthesizer=%s source_id=%s count=%s",
            self.name,
            source_id,
            len(existing_notes),
        )
        try:
            logger.info(
                "Sending synthesis request synthesizer=%s source_id=%s",
                self.name,
                source_id,
            )
            response = self.provider.complete_text(
                AITextRequest(
                    system_prompt=SYNTHESIS_SYSTEM_PROMPT,
                    user_prompt=_synthesis_user_prompt(
                        ref,
                        source_id,
                        evidence,
                        existing_notes,
                    ),
                    max_output_tokens=self.max_output_tokens,
                    temperature=0,
                    prompt_cache=self.prompt_cache,
                )
            )
            logger.info(
                "Synthesis response received synthesizer=%s source_id=%s model=%s "
                "input_tokens=%s output_tokens=%s cache_creation_input_tokens=%s "
                "cache_read_input_tokens=%s",
                self.name,
                source_id,
                response.model,
                response.input_tokens,
                response.output_tokens,
                response.cache_creation_input_tokens,
                response.cache_read_input_tokens,
            )
            candidate = _parse_synthesis_candidate(response.text)
            logger.info(
                "Synthesis response parsed synthesizer=%s source_id=%s title=%s "
                "matched_note_path=%s match_confidence=%s citations=%s",
                self.name,
                source_id,
                candidate.title,
                candidate.matched_note_path,
                candidate.match_confidence,
                len(candidate.citations),
            )
            write_result = write_knowledge_note(
                vault_path,
                source_id=source_id,
                ref=ref,
                candidate=candidate,
                existing_notes=existing_notes,
                match_confidence_threshold=self.match_confidence_threshold,
                now=now,
            )
        except Exception as exc:
            logger.exception(
                "Knowledge synthesis failed synthesizer=%s source_id=%s",
                self.name,
                source_id,
            )
            return KnowledgeSynthesisResult.failed(
                synthesizer_name=self.name,
                source_id=source_id,
                error_message=str(exc),
            )

        logger.info(
            "Knowledge synthesis completed synthesizer=%s source_id=%s note_path=%s "
            "created=%s",
            self.name,
            source_id,
            write_result.path,
            write_result.created,
        )
        return KnowledgeSynthesisResult.completed(
            synthesizer_name=self.name,
            source_id=source_id,
            note=write_result,
            classification=candidate.classification,
            citations=candidate.citations,
            matched_note_path=candidate.matched_note_path,
            match_confidence=candidate.match_confidence,
            uncertainty=candidate.uncertainty,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_creation_input_tokens=response.cache_creation_input_tokens,
            cache_read_input_tokens=response.cache_read_input_tokens,
        )


@dataclass(frozen=True)
class _SynthesisEvidenceBlock:
    sequence: int
    source_sequence: int
    text: str
    location: str
    kind: str


@dataclass(frozen=True)
class _SynthesisCandidate:
    title: str
    classification: SourceClassification
    body: str
    matched_note_path: str | None
    match_confidence: float
    citations: tuple[KnowledgeCitation, ...]
    uncertainty: str | None


def read_existing_knowledge_notes(
    vault_path: Path,
) -> tuple[ExistingKnowledgeNote, ...]:
    """Read existing Markdown notes under the knowledge-note directory."""

    root = vault_path / KNOWLEDGE_NOTES_DIRECTORY
    if not root.exists():
        logger.info("Knowledge note directory does not exist path=%s", root)
        return ()

    notes: list[ExistingKnowledgeNote] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        metadata, body = _split_frontmatter(text)
        title = _metadata_string(metadata, "title") or _heading_title(body) or path.stem
        source_ids = _metadata_string_tuple(metadata, "source_ids")
        notes.append(
            ExistingKnowledgeNote(
                title=title,
                path=path,
                relative_path=path.relative_to(vault_path),
                note_id=_metadata_string(metadata, "note_id"),
                source_ids=source_ids,
                excerpt=_excerpt(body),
            )
        )
    return tuple(notes)


def write_knowledge_note(
    vault_path: Path,
    *,
    source_id: str,
    ref: ArchivedSourceRef,
    candidate: _SynthesisCandidate,
    existing_notes: tuple[ExistingKnowledgeNote, ...],
    match_confidence_threshold: float = SYNTHESIS_MATCH_CONFIDENCE_THRESHOLD,
    now: datetime | None = None,
) -> KnowledgeNoteWriteResult:
    """Create or update a knowledge note from a synthesis candidate."""

    created_at = format_datetime(now or datetime.now(UTC))
    matched_note = _strong_matched_note(
        candidate,
        existing_notes,
        match_confidence_threshold,
    )
    if matched_note is None:
        note_id = _knowledge_note_id(candidate.title)
        target_path = _unused_knowledge_note_path(vault_path, candidate.title)
        existing_source_ids: tuple[str, ...] = ()
        created = True
        logger.info(
            "Creating knowledge note title=%s path=%s source_id=%s",
            candidate.title,
            target_path,
            source_id,
        )
    else:
        note_id = matched_note.note_id or _knowledge_note_id(candidate.title)
        target_path = matched_note.path
        existing_source_ids = matched_note.source_ids
        created = False
        logger.info(
            "Updating knowledge note title=%s path=%s source_id=%s "
            "matched_note_path=%s match_confidence=%s",
            candidate.title,
            target_path,
            source_id,
            candidate.matched_note_path,
            candidate.match_confidence,
        )

    source_ids = _append_unique(existing_source_ids, source_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        render_knowledge_note(
            candidate,
            source_id=source_id,
            source_ids=source_ids,
            note_id=note_id,
            ref=ref,
            created_at=created_at,
            updated_at=created_at,
        ),
        encoding="utf-8",
    )
    return KnowledgeNoteWriteResult(
        note_id=note_id,
        title=candidate.title,
        path=target_path,
        relative_path=target_path.relative_to(vault_path),
        created=created,
    )


def render_knowledge_note(
    candidate: _SynthesisCandidate,
    *,
    source_id: str,
    source_ids: tuple[str, ...],
    note_id: str,
    ref: ArchivedSourceRef,
    created_at: str,
    updated_at: str,
) -> str:
    """Render a knowledge note as Obsidian-compatible Markdown."""

    uncertainty = candidate.uncertainty
    if (
        uncertainty is None
        and candidate.match_confidence < SYNTHESIS_MATCH_CONFIDENCE_THRESHOLD
    ):
        uncertainty = "AI did not identify a strong existing-note match."

    frontmatter = _frontmatter(
        {
            "title": candidate.title,
            "type": "knowledge_note",
            "note_id": note_id,
            "source_ids": source_ids,
            "source_count": len(source_ids),
            "document_type": candidate.classification.document_type,
            "topics": candidate.classification.topics,
            "taxonomy": candidate.classification.taxonomy,
            "synthesis_status": SynthesisStatus.COMPLETED.value,
            "match_confidence": candidate.match_confidence,
            "uncertainty": uncertainty,
            "created_at": created_at,
            "updated_at": updated_at,
        }
    )
    return "\n".join(
        [
            frontmatter,
            f"# {candidate.title}",
            "",
            "## Summary",
            "",
            candidate.classification.summary,
            "",
            "## Details",
            "",
            candidate.body,
            "",
            "## Sources",
            "",
            *_render_citations(source_id, ref, candidate.citations),
            "",
        ]
    )


def _synthesis_evidence(
    extraction_result: ExtractionResult,
    enhancement_result: EnhancementResult | None,
) -> tuple[_SynthesisEvidenceBlock, ...]:
    if (
        enhancement_result is not None
        and enhancement_result.status is EnhancementStatus.COMPLETED
        and enhancement_result.enhanced_evidence
    ):
        return tuple(
            _SynthesisEvidenceBlock(
                sequence=block.sequence,
                source_sequence=block.source_sequence,
                text=block.text,
                location=block.location.label(),
                kind="enhanced",
            )
            for block in enhancement_result.enhanced_evidence
        )
    if extraction_result.status is not ExtractionStatus.COMPLETED:
        return ()
    return tuple(
        _SynthesisEvidenceBlock(
            sequence=block.sequence,
            source_sequence=block.sequence,
            text=block.text,
            location=block.location.label(),
            kind="extracted",
        )
        for block in extraction_result.evidence
    )


def _synthesis_user_prompt(
    ref: ArchivedSourceRef,
    source_id: str,
    evidence: tuple[_SynthesisEvidenceBlock, ...],
    existing_notes: tuple[ExistingKnowledgeNote, ...],
) -> str:
    evidence_payload = [
        {
            "sequence": block.sequence,
            "source_sequence": block.source_sequence,
            "kind": block.kind,
            "location": block.location,
            "text": block.text,
        }
        for block in evidence
    ]
    existing_notes_payload = [
        {
            "title": note.title,
            "path": note.relative_path.as_posix(),
            "note_id": note.note_id,
            "source_ids": note.source_ids,
            "excerpt": note.excerpt,
        }
        for note in existing_notes
    ]
    return "\n".join(
        [
            f"Source ID: {source_id}",
            f"Source filename: {ref.original_filename}",
            f"MIME type: {ref.mime_type}",
            "",
            "Existing knowledge notes JSON:",
            json.dumps(existing_notes_payload, indent=2, sort_keys=True),
            "",
            "Source evidence JSON:",
            json.dumps(evidence_payload, indent=2, sort_keys=True),
        ]
    )


def _parse_synthesis_candidate(response_text: str) -> _SynthesisCandidate:
    parsed: object = json.loads(_strip_json_fence(response_text))
    if not isinstance(parsed, dict):
        raise ValueError("Synthesis response must be a JSON object")

    title = _required_string(parsed, "title")
    summary = _required_string(parsed, "summary")
    body = _required_string(parsed, "body")
    classification = SourceClassification(
        document_type=_required_string(parsed, "document_type"),
        topics=_required_string_tuple(parsed, "topics"),
        taxonomy=_required_string_tuple(parsed, "taxonomy"),
        summary=summary,
    )
    return _SynthesisCandidate(
        title=title,
        classification=classification,
        body=body,
        matched_note_path=_optional_string(parsed, "matched_note_path"),
        match_confidence=_required_float(parsed, "match_confidence"),
        citations=_required_citations(parsed),
        uncertainty=_optional_string(parsed, "uncertainty"),
    )


def _required_string(data: dict[object, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Synthesis response must include "{key}"')
    return value.strip()


def _optional_string(data: dict[object, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'Synthesis response field "{key}" must be a string or null')
    stripped = value.strip()
    return stripped or None


def _required_string_tuple(data: dict[object, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f'Synthesis response field "{key}" must be a list')
    strings = tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )
    if not strings:
        raise ValueError(f'Synthesis response field "{key}" must include strings')
    return strings


def _required_float(data: dict[object, object], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f'Synthesis response field "{key}" must be a number')
    numeric = float(value)
    if numeric < 0 or numeric > 1:
        raise ValueError(f'Synthesis response field "{key}" must be between 0 and 1')
    return numeric


def _required_citations(data: dict[object, object]) -> tuple[KnowledgeCitation, ...]:
    value = data.get("citations")
    if not isinstance(value, list):
        raise ValueError('Synthesis response field "citations" must be a list')

    citations: list[KnowledgeCitation] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each synthesis citation must be an object")
        source_sequence = item.get("source_sequence")
        location = item.get("location")
        quote = item.get("quote")
        if not isinstance(source_sequence, int):
            raise ValueError('Each citation must include integer "source_sequence"')
        if not isinstance(location, str) or not location.strip():
            raise ValueError('Each citation must include string "location"')
        if quote is not None and not isinstance(quote, str):
            raise ValueError('Citation "quote" must be a string or null')
        citations.append(
            KnowledgeCitation(
                source_sequence=source_sequence,
                location=location.strip(),
                quote=None if quote is None else quote.strip() or None,
            )
        )

    if not citations:
        raise ValueError("Synthesis response must include at least one citation")
    return tuple(citations)


def _strong_matched_note(
    candidate: _SynthesisCandidate,
    existing_notes: tuple[ExistingKnowledgeNote, ...],
    match_confidence_threshold: float,
) -> ExistingKnowledgeNote | None:
    if (
        candidate.matched_note_path is None
        or candidate.match_confidence < match_confidence_threshold
    ):
        return None
    for note in existing_notes:
        if note.relative_path.as_posix() == candidate.matched_note_path:
            return note
    return None


def _unused_knowledge_note_path(vault_path: Path, title: str) -> Path:
    root = vault_path / KNOWLEDGE_NOTES_DIRECTORY
    base_slug = _slugify(title)
    candidate = root / f"{base_slug}.md"
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_slug}-{suffix}.md"
        suffix += 1
    return candidate


def _knowledge_note_id(title: str) -> str:
    return f"knowledge-{_slugify(title)}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "untitled"


def _append_unique(existing: tuple[str, ...], value: str) -> tuple[str, ...]:
    if value in existing:
        return existing
    return (*existing, value)


def _render_citations(
    source_id: str,
    ref: ArchivedSourceRef,
    citations: tuple[KnowledgeCitation, ...],
) -> list[str]:
    lines = [
        f"- [[{source_id}|{ref.original_filename}]]",
    ]
    for citation in citations:
        label = f"Evidence {citation.source_sequence}: {citation.location}"
        if citation.quote is not None:
            label += f" - {citation.quote}"
        lines.append(f"  - {label}")
    return lines


def _frontmatter(values: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, tuple):
        return json.dumps(list(value))
    return json.dumps(str(value))


def _split_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    end_index = markdown.find("\n---\n", 4)
    if end_index == -1:
        return {}, markdown

    metadata: dict[str, object] = {}
    frontmatter = markdown[4:end_index]
    body = markdown[end_index + len("\n---\n") :]
    for line in frontmatter.splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        metadata[key.strip()] = _parse_frontmatter_value(raw_value.strip())
    return metadata, body


def _parse_frontmatter_value(value: str) -> object:
    if not value:
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith('"') or value.startswith("["):
        parsed: object = json.loads(value)
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
        return parsed
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _metadata_string(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _metadata_string_tuple(metadata: dict[str, object], key: str) -> tuple[str, ...]:
    value = metadata.get(key)
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, str) and item.strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _heading_title(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


def _excerpt(body: str, limit: int = 1_200) -> str:
    compact = re.sub(r"\s+", " ", body).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


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
