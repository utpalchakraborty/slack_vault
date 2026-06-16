from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from slack_vault.retrieval import (
    ObsidianCliError,
    ObsidianCliSearch,
    build_answer_context,
    load_vault_index,
    obsidian_search,
    obsidian_search_many,
    split_frontmatter,
)


def test_load_vault_index_reads_notes_and_source_records(tmp_path: Path) -> None:
    _write_knowledge_note(
        tmp_path,
        slug="project-alpha-plan",
        title="Project Alpha Plan",
        source_ids=("source-alpha",),
        topics=("Project Alpha", "planning"),
        body="Project Alpha needs a local-first ingest path. #planning [[Alpha]]",
    )
    _write_source_record(
        tmp_path,
        source_id="source-alpha",
        title="Alpha Plan.docx",
        original_filename="Alpha Plan.docx",
    )

    index = load_vault_index(tmp_path)

    assert len(index.knowledge_notes) == 1
    note = index.knowledge_notes[0]
    assert note.title == "Project Alpha Plan"
    assert note.relative_path.as_posix() == "10 Knowledge/project-alpha-plan.md"
    assert note.source_ids == ("source-alpha",)
    assert note.topics == ("Project Alpha", "planning")
    assert note.tags == ("generated", "planning")
    assert note.wikilinks == ("Alpha",)
    assert note.document_type == "project_plan"
    assert "local-first ingest" in note.body

    assert len(index.source_records) == 1
    source_record = index.source_records[0]
    assert source_record.source_id == "source-alpha"
    assert source_record.original_filename == "Alpha Plan.docx"
    assert source_record.relative_path.as_posix() == (
        "20 Sources/sources/source-alpha.md"
    )
    assert source_record.evidence_artifact_uri == (
        ".data/archive/derived/evidence/source-alpha/evidence.json"
    )
    assert index.source_record_for("source-alpha") == source_record


def test_obsidian_search_uses_cli_hit_order_and_source_record_expansion(
    tmp_path: Path,
) -> None:
    _write_knowledge_note(
        tmp_path,
        slug="project-alpha-plan",
        title="Project Alpha Plan",
        source_ids=("source-alpha",),
        topics=("Project Alpha", "planning"),
        body="Project Alpha describes the current local archive and ingest state.",
    )
    _write_source_record(
        tmp_path,
        source_id="source-alpha",
        title="Alpha Requirements.docx",
        original_filename="Alpha Requirements.docx",
    )
    _write_knowledge_note(
        tmp_path,
        slug="hubspot-current-state",
        title="HubSpot Current State",
        source_ids=("source-hubspot",),
        topics=("HubSpot", "CRM"),
        body="HubSpot has duplicated account fields and manual routing.",
    )
    _write_source_record(
        tmp_path,
        source_id="source-hubspot",
        title="Hubspot - Current State.docx",
        original_filename="Hubspot - Current State.docx",
    )
    index = load_vault_index(tmp_path)
    search_provider = _FakeObsidianSearch(
        (
            Path("20 Sources/sources/source-hubspot.md"),
            Path("10 Knowledge/project-alpha-plan.md"),
        )
    )

    results = obsidian_search(
        index,
        "What is the HubSpot current state?",
        search_provider=search_provider,
    )

    assert [result.note.title for result in results] == [
        "HubSpot Current State",
        "Project Alpha Plan",
    ]
    assert results[0].score > results[1].score
    assert "hubspot" in results[0].matched_terms
    assert search_provider.calls == [("hubspot current state", 5)]

    source_id_results = obsidian_search(
        index,
        "source-alpha",
        search_provider=_FakeObsidianSearch(
            (Path("20 Sources/sources/source-alpha.md"),)
        ),
    )

    assert source_id_results[0].note.title == "Project Alpha Plan"


def test_obsidian_search_many_merges_ai_planned_query_hits(
    tmp_path: Path,
) -> None:
    _write_knowledge_note(
        tmp_path,
        slug="new-jersey-company-filings",
        title="New Jersey Company Filings",
        source_ids=("source-nj",),
        topics=("New Jersey filings",),
        body="Garden State Meridian Services has low company filing risk.",
    )
    _write_source_record(
        tmp_path,
        source_id="source-nj",
        title="sample_nj_company_filings_status.md",
        original_filename="sample_nj_company_filings_status.md",
    )
    search_provider = _FakeObsidianSearchByQuery(
        {
            "NJ company filings": (Path("20 Sources/sources/source-nj.md"),),
            "New Jersey company filings": (
                Path("10 Knowledge/new-jersey-company-filings.md"),
            ),
        }
    )

    results = obsidian_search_many(
        load_vault_index(tmp_path),
        ("NJ company filings", "New Jersey company filings"),
        search_provider=search_provider,
    )

    assert [result.note.title for result in results] == ["New Jersey Company Filings"]
    assert search_provider.calls == [
        ("NJ company filings", 5),
        ("New Jersey company filings", 5),
    ]


def test_build_answer_context_includes_source_paths_and_matching_excerpt(
    tmp_path: Path,
) -> None:
    _write_knowledge_note(
        tmp_path,
        slug="hubspot-current-state",
        title="HubSpot Current State",
        source_ids=("source-hubspot",),
        topics=("HubSpot", "CRM"),
        body=(
            "Introductory context.\n\n"
            "HubSpot has duplicated account fields and manual routing.\n\n"
            "Unrelated closing paragraph."
        ),
    )
    _write_source_record(
        tmp_path,
        source_id="source-hubspot",
        title="Hubspot - Current State.docx",
        original_filename="Hubspot - Current State.docx",
    )

    context = build_answer_context(
        load_vault_index(tmp_path),
        "Which HubSpot account fields are duplicated?",
        search_provider=_FakeObsidianSearch(
            (Path("10 Knowledge/hubspot-current-state.md"),)
        ),
        search_queries=("HubSpot account fields", "duplicated account fields"),
    )

    assert len(context.items) == 1
    item = context.items[0]
    assert item.citation_id == 1
    assert item.note_title == "HubSpot Current State"
    assert item.note_path.as_posix() == "10 Knowledge/hubspot-current-state.md"
    assert item.source_ids == ("source-hubspot",)
    assert item.source_record_paths[0].as_posix() == (
        "20 Sources/sources/source-hubspot.md"
    )
    assert "duplicated account fields" in item.excerpt
    assert context.search_query == ("HubSpot account fields, duplicated account fields")


def test_build_answer_context_returns_empty_when_query_has_no_match(
    tmp_path: Path,
) -> None:
    _write_knowledge_note(
        tmp_path,
        slug="project-alpha-plan",
        title="Project Alpha Plan",
        source_ids=("source-alpha",),
        topics=("Project Alpha",),
        body="Project Alpha describes local ingest.",
    )

    context = build_answer_context(
        load_vault_index(tmp_path),
        "quantum mechanics",
        search_provider=_FakeObsidianSearch(()),
    )

    assert context.items == ()


def test_obsidian_search_rejects_non_positive_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limit must be at least 1"):
        obsidian_search(
            load_vault_index(tmp_path),
            "anything",
            search_provider=_FakeObsidianSearch(()),
            limit=0,
        )


def test_obsidian_cli_search_executes_search_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeObsidianRunner(
        stdout='["10 Knowledge/project-alpha-plan.md"]\n',
    )
    monkeypatch.setattr(
        "slack_vault.retrieval.shutil.which",
        lambda executable: "/usr/local/bin/obsidian",
    )

    results = ObsidianCliSearch(
        vault_name="slack_obsidian",
        runner=runner,
    ).search("meeting notes", limit=3)

    assert results == (Path("10 Knowledge/project-alpha-plan.md"),)
    assert runner.commands == [
        (
            "obsidian",
            "search",
            "query=meeting notes",
            "vault=slack_obsidian",
            "limit=3",
            "format=json",
        ),
    ]


def test_obsidian_cli_search_reports_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "slack_vault.retrieval.shutil.which",
        lambda executable: None,
    )

    with pytest.raises(ObsidianCliError, match="Obsidian CLI is not installed"):
        ObsidianCliSearch(vault_name="slack_obsidian").search("anything", limit=5)


@pytest.mark.parametrize(
    ("stdout", "expected_error"),
    [
        ("", None),
        ("No matches found.", None),
        ("Vault not found.", "could not find the configured vault"),
        ("{}", "must be a list of paths"),
        ('["ok.md", 1]', "must contain only path strings"),
    ],
)
def test_obsidian_cli_search_rejects_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    expected_error: str | None,
) -> None:
    monkeypatch.setattr(
        "slack_vault.retrieval.shutil.which",
        lambda executable: "/usr/local/bin/obsidian",
    )

    searcher = ObsidianCliSearch(
        vault_name="slack_obsidian",
        runner=_FakeObsidianRunner(stdout=stdout),
    )
    if expected_error is None:
        assert searcher.search("anything", limit=5) == ()
    else:
        with pytest.raises(ObsidianCliError, match=expected_error):
            searcher.search("anything", limit=5)


def test_obsidian_cli_search_reports_command_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "slack_vault.retrieval.shutil.which",
        lambda executable: "/usr/local/bin/obsidian",
    )

    with pytest.raises(ObsidianCliError, match="Obsidian CLI search failed: no app"):
        ObsidianCliSearch(
            vault_name="slack_obsidian",
            runner=_FakeObsidianRunner(returncode=1, stderr="no app"),
        ).search("anything", limit=5)


def test_split_frontmatter_handles_generated_scalars_and_broken_fences() -> None:
    metadata, body = split_frontmatter(
        "\n".join(
            [
                "---",
                'title: "Generated Note"',
                'source_ids: ["source-a", "source-b"]',
                "source_count: 2",
                "match_confidence: 0.75",
                "flag: true",
                "empty:",
                "---",
                "",
                "# Body",
            ]
        )
    )

    assert metadata["title"] == "Generated Note"
    assert metadata["source_ids"] == ("source-a", "source-b")
    assert metadata["source_count"] == 2
    assert metadata["match_confidence"] == 0.75
    assert metadata["flag"] is True
    assert metadata["empty"] == ""
    assert body.startswith("\n# Body")

    broken_metadata, broken_body = split_frontmatter(
        '---\ntitle: "Missing close"\n# Heading\n'
    )

    assert broken_metadata == {}
    assert broken_body.startswith("---")


def _write_knowledge_note(
    vault_path: Path,
    *,
    slug: str,
    title: str,
    source_ids: tuple[str, ...],
    topics: tuple[str, ...],
    body: str,
) -> None:
    note_path = vault_path / "10 Knowledge" / f"{slug}.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    source_ids_json = _json_array(source_ids)
    topics_json = _json_array(topics)
    note_path.write_text(
        "\n".join(
            [
                "---",
                f'title: "{title}"',
                'type: "knowledge_note"',
                f'note_id: "knowledge-{slug}"',
                f"source_ids: {source_ids_json}",
                f"source_count: {len(source_ids)}",
                'document_type: "project_plan"',
                f"topics: {topics_json}",
                'taxonomy: ["projects"]',
                'tags: ["generated"]',
                "---",
                "",
                f"# {title}",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_source_record(
    vault_path: Path,
    *,
    source_id: str,
    title: str,
    original_filename: str,
) -> None:
    source_path = vault_path / "20 Sources/sources" / f"{source_id}.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "\n".join(
            [
                "---",
                f'title: "{title}"',
                'type: "source_record"',
                f'source_id: "{source_id}"',
                f'original_filename: "{original_filename}"',
                f'archive_uri: "/archive/{source_id}/original"',
                "evidence_artifact_uri: "
                f'".data/archive/derived/evidence/{source_id}/evidence.json"',
                "---",
                "",
                f"# {title}",
                "",
                "Source provenance.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _json_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


class _FakeObsidianSearch:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int) -> tuple[Path, ...]:
        self.calls.append((query, limit))
        return self.paths[:limit]


class _FakeObsidianSearchByQuery:
    def __init__(self, paths_by_query: dict[str, tuple[Path, ...]]) -> None:
        self.paths_by_query = paths_by_query
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int) -> tuple[Path, ...]:
        self.calls.append((query, limit))
        return self.paths_by_query.get(query, ())[:limit]


class _FakeObsidianRunner:
    def __init__(
        self,
        *,
        stdout: str = "[]",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )
