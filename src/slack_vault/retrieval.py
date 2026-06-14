"""Obsidian vault reading and search."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from slack_vault.source_registry import SOURCE_RECORDS_DIRECTORY
from slack_vault.synthesis import KNOWLEDGE_NOTES_DIRECTORY

DEFAULT_RETRIEVAL_LIMIT = 5
DEFAULT_EXCERPT_LIMIT = 900

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_TAG_PATTERN = re.compile(r"(?<![\w/])#([a-zA-Z0-9][\w/-]*)")
_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "be",
        "can",
        "did",
        "do",
        "does",
        "document",
        "documents",
        "for",
        "from",
        "give",
        "in",
        "is",
        "me",
        "of",
        "on",
        "or",
        "please",
        "say",
        "says",
        "show",
        "summary",
        "summarize",
        "tell",
        "that",
        "the",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "when",
        "where",
        "which",
        "who",
        "why",
        "what",
        "with",
        "you",
    }
)
ObsidianCommandRunner = Callable[
    [Sequence[str]],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True)
class VaultKnowledgeNote:
    """A generated knowledge note loaded from the Obsidian vault."""

    title: str
    path: Path
    relative_path: Path
    metadata: dict[str, object]
    body: str
    note_id: str | None
    source_ids: tuple[str, ...]
    topics: tuple[str, ...]
    taxonomy: tuple[str, ...]
    tags: tuple[str, ...]
    wikilinks: tuple[str, ...]
    document_type: str | None


@dataclass(frozen=True)
class VaultSourceRecord:
    """A lightweight source record loaded from the Obsidian vault."""

    title: str
    path: Path
    relative_path: Path
    metadata: dict[str, object]
    body: str
    source_id: str
    original_filename: str | None
    archive_uri: str | None
    evidence_artifact_uri: str | None


@dataclass(frozen=True)
class VaultIndex:
    """Loaded vault notes and source records used for local retrieval."""

    vault_path: Path
    knowledge_notes: tuple[VaultKnowledgeNote, ...]
    source_records: tuple[VaultSourceRecord, ...]

    def source_record_for(self, source_id: str) -> VaultSourceRecord | None:
        """Return the source record for a source ID when present."""

        for source_record in self.source_records:
            if source_record.source_id == source_id:
                return source_record
        return None


@dataclass(frozen=True)
class RetrievalResult:
    """An Obsidian search hit and its source-record provenance."""

    note: VaultKnowledgeNote
    score: float
    matched_terms: tuple[str, ...]
    source_records: tuple[VaultSourceRecord, ...]


class ObsidianSearchProvider(Protocol):
    """Search provider backed by Obsidian's vault index."""

    def search(self, query: str, *, limit: int) -> tuple[Path, ...]:
        """Return vault-relative search hit paths."""
        ...


class ObsidianCliError(RuntimeError):
    """Raised when Obsidian CLI search cannot be used."""


def _run_obsidian_command(
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class ObsidianCliSearch:
    """Obsidian CLI backed search provider."""

    vault_name: str
    executable: str = "obsidian"
    runner: ObsidianCommandRunner = _run_obsidian_command

    def search(self, query: str, *, limit: int) -> tuple[Path, ...]:
        """Run Obsidian search and return vault-relative paths."""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        if shutil.which(self.executable) is None:
            raise ObsidianCliError(
                "Obsidian CLI is not installed or not on PATH. "
                "Enable Command line interface in Obsidian Settings > General "
                "and register the CLI."
            )

        completed = self.runner(
            (
                self.executable,
                "search",
                f"query={query}",
                f"vault={self.vault_name}",
                f"limit={limit}",
                "format=json",
            )
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise ObsidianCliError(
                "Obsidian CLI search failed" + (f": {message}" if message else ".")
            )

        return _parse_obsidian_search_paths(completed.stdout)


@dataclass(frozen=True)
class AnswerContextItem:
    """A compact Obsidian search hit for grounded answer generation."""

    citation_id: int
    note_title: str
    note_path: Path
    source_ids: tuple[str, ...]
    source_record_paths: tuple[Path, ...]
    tags: tuple[str, ...]
    wikilinks: tuple[str, ...]
    excerpt: str
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class AnswerContext:
    """Citation-aware context for a local vault question."""

    question: str
    search_query: str
    items: tuple[AnswerContextItem, ...]


def load_vault_index(vault_path: Path) -> VaultIndex:
    """Load generated knowledge notes and source records from a vault."""

    expanded_vault_path = vault_path.expanduser()
    return VaultIndex(
        vault_path=expanded_vault_path,
        knowledge_notes=read_knowledge_notes(expanded_vault_path),
        source_records=read_source_records(expanded_vault_path),
    )


def read_knowledge_notes(vault_path: Path) -> tuple[VaultKnowledgeNote, ...]:
    """Read generated Markdown knowledge notes from the vault."""

    root = vault_path / KNOWLEDGE_NOTES_DIRECTORY
    if not root.exists():
        return ()

    notes: list[VaultKnowledgeNote] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        metadata, body = _read_markdown(path)
        title = _metadata_string(metadata, "title") or _heading_title(body) or path.stem
        tags = _extract_tags(metadata, body)
        notes.append(
            VaultKnowledgeNote(
                title=title,
                path=path,
                relative_path=path.relative_to(vault_path),
                metadata=metadata,
                body=body,
                note_id=_metadata_string(metadata, "note_id"),
                source_ids=_metadata_string_tuple(metadata, "source_ids"),
                topics=_metadata_string_tuple(metadata, "topics"),
                taxonomy=_metadata_string_tuple(metadata, "taxonomy"),
                tags=tags,
                wikilinks=_extract_wikilinks(body),
                document_type=_metadata_string(metadata, "document_type"),
            )
        )
    return tuple(notes)


def read_source_records(vault_path: Path) -> tuple[VaultSourceRecord, ...]:
    """Read lightweight Markdown source records from the vault."""

    root = vault_path / SOURCE_RECORDS_DIRECTORY
    if not root.exists():
        return ()

    source_records: list[VaultSourceRecord] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        metadata, body = _read_markdown(path)
        title = _metadata_string(metadata, "title") or _heading_title(body) or path.stem
        source_id = _metadata_string(metadata, "source_id") or path.stem
        source_records.append(
            VaultSourceRecord(
                title=title,
                path=path,
                relative_path=path.relative_to(vault_path),
                metadata=metadata,
                body=body,
                source_id=source_id,
                original_filename=_metadata_string(metadata, "original_filename"),
                archive_uri=_metadata_string(metadata, "archive_uri"),
                evidence_artifact_uri=_metadata_string(
                    metadata,
                    "evidence_artifact_uri",
                ),
            )
        )
    return tuple(source_records)


def lexical_search(
    index: VaultIndex,
    query: str,
    *,
    search_provider: ObsidianSearchProvider,
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
) -> tuple[RetrievalResult, ...]:
    """Compatibility wrapper for Obsidian CLI search."""

    return obsidian_search(
        index,
        query,
        search_provider=search_provider,
        limit=limit,
    )


def obsidian_search(
    index: VaultIndex,
    query: str,
    *,
    search_provider: ObsidianSearchProvider,
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
) -> tuple[RetrievalResult, ...]:
    """Search Obsidian-indexed vault content through Obsidian CLI."""

    if limit < 1:
        raise ValueError("limit must be at least 1")

    terms = _query_terms(query)
    if not terms:
        return ()

    hit_paths = search_provider.search(_obsidian_search_query(query), limit=limit)
    return _search_results_from_paths(index, hit_paths, terms, limit=limit)


def _search_results_from_paths(
    index: VaultIndex,
    hit_paths: tuple[Path, ...],
    terms: tuple[str, ...],
    *,
    limit: int,
) -> tuple[RetrievalResult, ...]:
    source_record_by_id = {
        source_record.source_id: source_record for source_record in index.source_records
    }
    note_by_path = {
        note.relative_path.as_posix(): note for note in index.knowledge_notes
    }
    source_record_by_path = {
        source_record.relative_path.as_posix(): source_record
        for source_record in index.source_records
    }
    notes_by_source_id: dict[str, list[VaultKnowledgeNote]] = {}
    for note in index.knowledge_notes:
        for source_id in note.source_ids:
            notes_by_source_id.setdefault(source_id, []).append(note)

    results_by_path: dict[str, RetrievalResult] = {}
    for position, hit_path in enumerate(hit_paths):
        if len(results_by_path) >= limit:
            break
        for note in _notes_for_obsidian_hit(
            hit_path,
            note_by_path=note_by_path,
            source_record_by_path=source_record_by_path,
            notes_by_source_id=notes_by_source_id,
        ):
            if len(results_by_path) >= limit:
                break
            note_key = note.relative_path.as_posix()
            if note_key in results_by_path:
                continue
            source_records = tuple(
                source_record
                for source_id in note.source_ids
                if (source_record := source_record_by_id.get(source_id)) is not None
            )
            results_by_path[note_key] = RetrievalResult(
                note=note,
                score=float(len(hit_paths) - position),
                matched_terms=_matched_terms(
                    terms,
                    _combined_search_text(note, source_records),
                ),
                source_records=source_records,
            )

    return tuple(results_by_path.values())


def _notes_for_obsidian_hit(
    hit_path: Path,
    *,
    note_by_path: dict[str, VaultKnowledgeNote],
    source_record_by_path: dict[str, VaultSourceRecord],
    notes_by_source_id: dict[str, list[VaultKnowledgeNote]],
) -> tuple[VaultKnowledgeNote, ...]:
    normalized_path = hit_path.as_posix()
    direct_note = note_by_path.get(normalized_path)
    if direct_note is not None:
        return (direct_note,)

    source_record = source_record_by_path.get(normalized_path)
    if source_record is None:
        return ()
    return tuple(notes_by_source_id.get(source_record.source_id, ()))


def _parse_obsidian_search_paths(stdout: str) -> tuple[Path, ...]:
    if stdout.strip() == "No matches found.":
        return ()

    try:
        parsed: object = json.loads(stdout)
    except json.JSONDecodeError as exc:
        snippet = stdout.strip()
        raise ObsidianCliError(
            "Obsidian CLI search did not return JSON"
            + (f": {snippet}" if snippet else ".")
        ) from exc

    if not isinstance(parsed, list):
        raise ObsidianCliError("Obsidian CLI search JSON must be a list of paths.")

    paths: list[Path] = []
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            raise ObsidianCliError(
                "Obsidian CLI search JSON must contain only path strings."
            )
        paths.append(Path(item.strip()))
    return tuple(paths)


def build_answer_context(
    index: VaultIndex,
    question: str,
    *,
    search_provider: ObsidianSearchProvider,
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
    excerpt_limit: int = DEFAULT_EXCERPT_LIMIT,
) -> AnswerContext:
    """Build compact, citation-aware context for answer generation."""

    terms = _query_terms(question)
    results = obsidian_search(
        index,
        question,
        search_provider=search_provider,
        limit=limit,
    )
    items = tuple(
        AnswerContextItem(
            citation_id=citation_id,
            note_title=result.note.title,
            note_path=result.note.relative_path,
            source_ids=result.note.source_ids,
            source_record_paths=tuple(
                source_record.relative_path for source_record in result.source_records
            ),
            tags=result.note.tags,
            wikilinks=result.note.wikilinks,
            excerpt=_best_excerpt(result.note.body, terms, limit=excerpt_limit),
            score=result.score,
            matched_terms=result.matched_terms,
        )
        for citation_id, result in enumerate(results, start=1)
    )
    return AnswerContext(
        question=question,
        search_query=_obsidian_search_query(question),
        items=items,
    )


def split_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
    """Split generated Markdown frontmatter from the body."""

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


def _read_markdown(path: Path) -> tuple[dict[str, object], str]:
    return split_frontmatter(path.read_text(encoding="utf-8"))


def _weighted_term_score(
    terms: tuple[str, ...],
    text: str,
    *,
    weight: float,
) -> float:
    counts = Counter(_tokens(text))
    return sum(counts[term] * weight for term in terms)


def _matched_terms(terms: tuple[str, ...], text: str) -> tuple[str, ...]:
    available_terms = set(_tokens(text))
    return tuple(term for term in terms if term in available_terms)


def _combined_search_text(
    note: VaultKnowledgeNote,
    source_records: tuple[VaultSourceRecord, ...],
) -> str:
    return " ".join(
        (
            note.title,
            note.relative_path.as_posix(),
            _metadata_search_text(note.metadata),
            " ".join(note.tags),
            " ".join(note.wikilinks),
            note.body,
            *(_source_record_search_text(record) for record in source_records),
        )
    )


def _source_record_search_text(source_record: VaultSourceRecord) -> str:
    return " ".join(
        (
            source_record.title,
            source_record.source_id,
            source_record.original_filename or "",
            source_record.archive_uri or "",
            source_record.evidence_artifact_uri or "",
            _metadata_search_text(source_record.metadata),
            source_record.body,
        )
    )


def _metadata_search_text(metadata: dict[str, object]) -> str:
    return " ".join(_flatten_metadata_value(value) for value in metadata.values())


def _flatten_metadata_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, tuple | list):
        return " ".join(_flatten_metadata_value(item) for item in value)
    return ""


def _best_excerpt(
    body: str,
    terms: tuple[str, ...],
    *,
    limit: int,
) -> str:
    blocks = _body_blocks(body)
    if not blocks:
        return ""
    best_block = max(
        blocks, key=lambda block: _weighted_term_score(terms, block, weight=1.0)
    )
    return _truncate(_normalize_space(best_block), limit)


def _body_blocks(body: str) -> tuple[str, ...]:
    blocks = tuple(
        _normalize_space(block)
        for block in re.split(r"\n\s*\n", body)
        if _normalize_space(block)
    )
    if blocks:
        return blocks
    compact = _normalize_space(body)
    return () if not compact else (compact,)


def _truncate(value: str, limit: int) -> str:
    if limit < 1:
        return ""
    if len(value) <= limit:
        return value
    if limit < 4:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _query_terms(query: str) -> tuple[str, ...]:
    tokens = _tokens(query)
    filtered = tuple(
        token for token in tokens if token not in _STOP_WORDS and len(token) > 1
    )
    return tuple(dict.fromkeys(filtered or tokens))


def _obsidian_search_query(question: str) -> str:
    return " ".join(_query_terms(question))


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_PATTERN.findall(text.lower()))


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_frontmatter_value(value: str) -> object:
    if not value:
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith('"') or value.startswith("["):
        try:
            parsed: object = json.loads(value)
        except json.JSONDecodeError:
            return value
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
        return tuple(item.strip() for item in value if item.strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _extract_tags(metadata: dict[str, object], body: str) -> tuple[str, ...]:
    metadata_tags = tuple(
        tag.lstrip("#").strip()
        for tag in _metadata_string_tuple(metadata, "tags")
        if tag.lstrip("#").strip()
    )
    body_tags = tuple(match.group(1).strip() for match in _TAG_PATTERN.finditer(body))
    return tuple(dict.fromkeys((*metadata_tags, *body_tags)))


def _extract_wikilinks(body: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in _WIKILINK_PATTERN.finditer(body):
        target = match.group(1).strip()
        alias = None if match.group(2) is None else match.group(2).strip()
        if target:
            values.append(target)
        if alias:
            values.append(alias)
    return tuple(dict.fromkeys(values))


def _heading_title(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None
