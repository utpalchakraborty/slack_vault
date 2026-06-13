from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from slack_vault.ai import AITextRequest, AITextResponse
from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import ArchiveProviderKind
from slack_vault.enhancement import (
    EnhancedEvidenceBlock,
    EnhancementResult,
)
from slack_vault.extraction import (
    EvidenceBlock,
    EvidenceLocation,
    EvidenceLocationKind,
    ExtractionResult,
)
from slack_vault.synthesis import (
    AnthropicKnowledgeSynthesizer,
    SynthesisStatus,
    read_existing_knowledge_notes,
)


def test_synthesizer_creates_knowledge_note_from_ai_response(tmp_path: Path) -> None:
    provider = _FakeTextProvider(
        _synthesis_response(
            title="Project Alpha Plan",
            matched_note_path=None,
            match_confidence=0.0,
        )
    )
    source_id = "source-2026-06-13-abcdef123456"

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        source_id,
        _extraction_result(),
        now=datetime(2026, 6, 13, 12, 30, tzinfo=UTC),
    )

    assert result.status is SynthesisStatus.COMPLETED
    assert result.note is not None
    assert result.note.created is True
    assert result.note.relative_path.as_posix() == "10 Knowledge/project-alpha-plan.md"
    assert result.classification is not None
    assert result.classification.document_type == "project_plan"
    assert result.citations[0].source_sequence == 1
    assert provider.requests
    assert "Source evidence JSON" in provider.requests[0].user_prompt

    markdown = result.note.path.read_text(encoding="utf-8")
    assert 'title: "Project Alpha Plan"' in markdown
    assert 'type: "knowledge_note"' in markdown
    assert 'source_ids: ["source-2026-06-13-abcdef123456"]' in markdown
    assert 'document_type: "project_plan"' in markdown
    assert 'topics: ["Project Alpha", "planning"]' in markdown
    assert "# Project Alpha Plan" in markdown
    assert "Project Alpha needs a local-first ingest path." in markdown
    assert "[[source-2026-06-13-abcdef123456|Example Plan.md]]" in markdown
    assert 'Evidence 1: Example Plan.md, heading "Overview"' in markdown


def test_synthesizer_updates_existing_note_on_strong_match(tmp_path: Path) -> None:
    note_path = tmp_path / "10 Knowledge/project-alpha-plan.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "\n".join(
            [
                "---",
                'title: "Project Alpha Plan"',
                'type: "knowledge_note"',
                'note_id: "knowledge-project-alpha-plan"',
                'source_ids: ["source-old"]',
                "---",
                "",
                "# Project Alpha Plan",
                "",
                "Old body.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    provider = _FakeTextProvider(
        _synthesis_response(
            title="Project Alpha Plan",
            matched_note_path="10 Knowledge/project-alpha-plan.md",
            match_confidence=0.91,
            body="Updated project plan body.",
        )
    )

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        _extraction_result(),
        now=datetime(2026, 6, 13, 12, 45, tzinfo=UTC),
    )

    assert result.status is SynthesisStatus.COMPLETED
    assert result.note is not None
    assert result.note.created is False
    assert result.note.path == note_path

    markdown = note_path.read_text(encoding="utf-8")
    assert 'source_ids: ["source-old", "source-new"]' in markdown
    assert "Updated project plan body." in markdown
    assert "Old body." not in markdown


def test_synthesizer_does_not_duplicate_existing_source_id(tmp_path: Path) -> None:
    note_path = tmp_path / "10 Knowledge/project-alpha-plan.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "\n".join(
            [
                "---",
                'title: "Project Alpha Plan"',
                'source_ids: ["source-new"]',
                "---",
                "",
                "# Project Alpha Plan",
            ]
        ),
        encoding="utf-8",
    )
    provider = _FakeTextProvider(
        _synthesis_response(
            title="Project Alpha Plan",
            matched_note_path="10 Knowledge/project-alpha-plan.md",
            match_confidence=0.91,
        )
    )

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        _extraction_result(),
    )

    assert result.status is SynthesisStatus.COMPLETED
    assert 'source_ids: ["source-new"]' in note_path.read_text(encoding="utf-8")


def test_synthesizer_creates_new_note_for_low_confidence_match(
    tmp_path: Path,
) -> None:
    note_path = tmp_path / "10 Knowledge/project-alpha-plan.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("# Existing\n", encoding="utf-8")
    provider = _FakeTextProvider(
        _synthesis_response(
            title="Project Alpha Plan",
            matched_note_path="10 Knowledge/project-alpha-plan.md",
            match_confidence=0.25,
        )
    )

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        _extraction_result(),
    )

    assert result.status is SynthesisStatus.COMPLETED
    assert result.note is not None
    assert result.note.created is True
    assert (
        result.note.relative_path.as_posix() == "10 Knowledge/project-alpha-plan-2.md"
    )
    markdown = result.note.path.read_text(encoding="utf-8")
    assert (
        'uncertainty: "AI did not identify a strong existing-note match."' in markdown
    )


def test_synthesizer_uses_enhanced_evidence_when_available(tmp_path: Path) -> None:
    provider = _FakeTextProvider(
        _synthesis_response(
            title="Enhanced Plan",
            matched_note_path=None,
            match_confidence=0.0,
        )
    )

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        _extraction_result(),
        _enhancement_result(),
    )

    assert result.status is SynthesisStatus.COMPLETED
    assert "Enhanced source evidence" in provider.requests[0].user_prompt
    assert '"kind": "enhanced"' in provider.requests[0].user_prompt


def test_synthesizer_records_invalid_ai_response_as_failure(tmp_path: Path) -> None:
    provider = _FakeTextProvider("{}")

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        _extraction_result(),
    )

    assert result.status is SynthesisStatus.FAILED
    assert result.error_message is not None
    assert 'Synthesis response must include "title"' in result.error_message
    assert not (tmp_path / "10 Knowledge").exists()


def test_synthesizer_skips_when_no_evidence_is_available(tmp_path: Path) -> None:
    provider = _FakeTextProvider(
        _synthesis_response(
            title="Unused",
            matched_note_path=None,
            match_confidence=0.0,
        )
    )

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        ExtractionResult.failed(
            extractor_name="pdf",
            error_message="broken",
        ),
    )

    assert result.status is SynthesisStatus.SKIPPED
    assert result.error_message == (
        "No extracted or enhanced evidence is available for synthesis."
    )
    assert provider.requests == []


@pytest.mark.parametrize(
    ("response_payload", "expected_error"),
    [
        ([], "Synthesis response must be a JSON object"),
        (
            {
                "matched_note_path": 123,
            },
            'Synthesis response field "matched_note_path" must be a string or null',
        ),
        (
            {
                "topics": "not-a-list",
            },
            'Synthesis response field "topics" must be a list',
        ),
        (
            {
                "taxonomy": [],
            },
            'Synthesis response field "taxonomy" must include strings',
        ),
        (
            {
                "match_confidence": "high",
            },
            'Synthesis response field "match_confidence" must be a number',
        ),
        (
            {
                "match_confidence": 1.2,
            },
            'Synthesis response field "match_confidence" must be between 0 and 1',
        ),
        (
            {
                "citations": "not-a-list",
            },
            'Synthesis response field "citations" must be a list',
        ),
        (
            {
                "citations": ["not-an-object"],
            },
            "Each synthesis citation must be an object",
        ),
        (
            {
                "citations": [
                    {
                        "source_sequence": "1",
                        "location": "Example Plan.md",
                    }
                ],
            },
            'Each citation must include integer "source_sequence"',
        ),
        (
            {
                "citations": [
                    {
                        "source_sequence": 1,
                        "location": "",
                    }
                ],
            },
            'Each citation must include string "location"',
        ),
        (
            {
                "citations": [
                    {
                        "source_sequence": 1,
                        "location": "Example Plan.md",
                        "quote": 123,
                    }
                ],
            },
            'Citation "quote" must be a string or null',
        ),
        (
            {
                "citations": [],
            },
            "Synthesis response must include at least one citation",
        ),
    ],
)
def test_synthesizer_records_response_shape_errors(
    tmp_path: Path,
    response_payload: object,
    expected_error: str,
) -> None:
    payload: object
    if isinstance(response_payload, dict):
        payload = _complete_response_payload() | response_payload
    else:
        payload = response_payload
    provider = _FakeTextProvider(json.dumps(payload))

    result = AnthropicKnowledgeSynthesizer(provider).synthesize(
        tmp_path,
        _archived_source_ref(),
        "source-new",
        _extraction_result(),
    )

    assert result.status is SynthesisStatus.FAILED
    assert result.error_message == expected_error


def test_read_existing_knowledge_notes_handles_frontmatter_and_fallback_title(
    tmp_path: Path,
) -> None:
    notes_root = tmp_path / "10 Knowledge"
    notes_root.mkdir(parents=True)
    (notes_root / "frontmatter.md").write_text(
        "\n".join(
            [
                "---",
                'title: "Frontmatter Title"',
                'note_id: "knowledge-frontmatter-title"',
                'source_ids: ["source-a", "source-b"]',
                "source_count: 2",
                "---",
                "",
                "# Ignored Title",
                "",
                "Body text.",
            ]
        ),
        encoding="utf-8",
    )
    (notes_root / "heading.md").write_text(
        "# Heading Title\n\nBody text.", encoding="utf-8"
    )
    (notes_root / "scalar-source.md").write_text(
        "\n".join(
            [
                "---",
                "source_ids: source-scalar",
                "confidence: 0.5",
                "count: 2",
                "flag: true",
                "empty:",
                "malformed frontmatter line",
                "---",
                "",
                "Body text without heading.",
            ]
        ),
        encoding="utf-8",
    )
    (notes_root / "broken.md").write_text(
        '---\ntitle: "Missing Closing Fence"\n# Broken Heading\n',
        encoding="utf-8",
    )

    notes = read_existing_knowledge_notes(tmp_path)

    assert [note.title for note in notes] == [
        "Broken Heading",
        "Frontmatter Title",
        "Heading Title",
        "scalar-source",
    ]
    assert notes[0].source_ids == ()
    assert notes[0].note_id is None
    assert notes[1].note_id == "knowledge-frontmatter-title"
    assert notes[1].source_ids == ("source-a", "source-b")
    assert notes[2].relative_path.as_posix() == "10 Knowledge/heading.md"
    assert notes[3].source_ids == ("source-scalar",)


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


def _synthesis_response(
    *,
    title: str,
    matched_note_path: str | None,
    match_confidence: float,
    body: str = "Project Alpha needs a local-first ingest path.",
) -> str:
    return (
        "```json\n"
        + json.dumps(
            _complete_response_payload()
            | {
                "title": title,
                "body": body,
                "matched_note_path": matched_note_path,
                "match_confidence": match_confidence,
            },
            sort_keys=True,
        )
        + "\n```"
    )


def _complete_response_payload() -> dict[str, object]:
    return {
        "title": "Project Alpha Plan",
        "document_type": "project_plan",
        "topics": ["Project Alpha", "planning"],
        "taxonomy": ["projects", "implementation"],
        "summary": "Project Alpha plans local-first ingestion.",
        "body": "Project Alpha needs a local-first ingest path.",
        "matched_note_path": None,
        "match_confidence": 0.0,
        "citations": [
            {
                "source_sequence": 1,
                "location": 'Example Plan.md, heading "Overview"',
                "quote": "Local-first ingest path.",
            }
        ],
        "uncertainty": None,
    }


def _archived_source_ref() -> ArchivedSourceRef:
    return ArchivedSourceRef(
        archive_provider=ArchiveProviderKind.LOCAL,
        archive_id="sources/2026/06/abcdef1234567890",
        uri="/archive/sources/2026/06/abcdef1234567890/original",
        content_hash="abcdef1234567890",
        original_filename="Example Plan.md",
        mime_type="text/markdown",
        size_bytes=12,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
        ingestion_method="local_file",
        original_path="/tmp/Example Plan.md",
        uploaded_by="local-user",
    )


def _extraction_result() -> ExtractionResult:
    return ExtractionResult.completed(
        extractor_name="markdown",
        evidence=(
            EvidenceBlock(
                sequence=1,
                text="# Overview\n\nLocal-first ingest path.",
                location=EvidenceLocation(
                    kind=EvidenceLocationKind.HEADING,
                    file_name="Example Plan.md",
                    heading="Overview",
                ),
            ),
        ),
    )


def _enhancement_result() -> EnhancementResult:
    return EnhancementResult.completed(
        enhancer_name="anthropic",
        enhanced_evidence=(
            EnhancedEvidenceBlock(
                sequence=1,
                source_sequence=1,
                text="Enhanced source evidence.",
                location=EvidenceLocation(
                    kind=EvidenceLocationKind.HEADING,
                    file_name="Example Plan.md",
                    heading="Overview",
                ),
            ),
        ),
        model="fake-model",
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
