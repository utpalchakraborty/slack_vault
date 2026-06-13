from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from slack_vault.ai import (
    AIPromptCacheConfig,
    AITextRequest,
    AITextResponse,
    AnthropicAIProvider,
)
from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import ArchiveProviderKind, Settings
from slack_vault.enhancement import (
    AnthropicEvidenceEnhancer,
    EnhancementStatus,
)
from slack_vault.extraction import (
    EvidenceBlock,
    EvidenceLocation,
    EvidenceLocationKind,
    ExtractionResult,
)
from slack_vault.ingest import ingest_local_file


def test_anthropic_enhancer_parses_json_and_preserves_source_anchor() -> None:
    provider = _FakeTextProvider(
        AITextResponse(
            text='```json\n{"enhanced_text": "Clean **evidence**."}\n```',
            model="claude-test-model",
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=80,
            cache_read_input_tokens=40,
        )
    )
    enhancer = AnthropicEvidenceEnhancer(
        provider=provider,
        max_output_tokens=512,
        prompt_cache=AIPromptCacheConfig(automatic=False, cache_system_prompt=True),
    )

    result = enhancer.enhance(_archived_source_ref(), _extraction_result())

    assert result.status is EnhancementStatus.COMPLETED
    assert result.model == "claude-test-model"
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.cache_creation_input_tokens == 80
    assert result.cache_read_input_tokens == 40
    assert len(result.enhanced_evidence) == 1
    enhanced = result.enhanced_evidence[0]
    assert enhanced.sequence == 1
    assert enhanced.source_sequence == 1
    assert enhanced.text == "Clean **evidence**."
    assert enhanced.location.label() == 'Example Plan.md, heading "Overview"'
    assert provider.requests[0].max_output_tokens == 512
    assert provider.requests[0].temperature == 0
    assert provider.requests[0].prompt_cache == AIPromptCacheConfig(
        automatic=False,
        cache_system_prompt=True,
    )
    assert "Source location: Example Plan.md" in provider.requests[0].user_prompt


def test_anthropic_enhancer_skips_failed_extraction() -> None:
    provider = _FakeTextProvider(
        AITextResponse(
            text='{"enhanced_text": "unused"}',
            model="claude-test-model",
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    enhancer = AnthropicEvidenceEnhancer(provider=provider)

    result = enhancer.enhance(
        _archived_source_ref(),
        ExtractionResult.failed(
            extractor_name="pdf",
            error_message="broken pdf",
        ),
    )

    assert result.status is EnhancementStatus.SKIPPED
    assert result.error_message == "Extraction status is failed."
    assert provider.requests == []


def test_anthropic_enhancer_records_invalid_ai_response_as_failure() -> None:
    provider = _FakeTextProvider(
        AITextResponse(
            text="not json",
            model="claude-test-model",
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    enhancer = AnthropicEvidenceEnhancer(provider=provider)

    result = enhancer.enhance(_archived_source_ref(), _extraction_result())

    assert result.status is EnhancementStatus.FAILED
    assert "Expecting value" in (result.error_message or "")


def test_anthropic_live_enhancement_ingest_smoke_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("SLACK_VAULT_RUN_LIVE_AI_TESTS") != "1":
        pytest.skip("set SLACK_VAULT_RUN_LIVE_AI_TESTS=1 to run live AI tests")

    source = tmp_path / "phase-2b-live.md"
    source.write_text(
        "# Live Enhancement\n\n"
        "Critical evidence token: slack-vault-phase-2b-live-ok.\n"
        "Owner: Test Harness.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SLACK_VAULT_ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(tmp_path / "vault"))
    settings = Settings.from_env()
    enhancer = AnthropicEvidenceEnhancer(
        AnthropicAIProvider.from_settings(settings),
        max_output_tokens=256,
    )

    result = ingest_local_file(source, settings, evidence_enhancer=enhancer)

    assert result.enhancement_result is not None
    assert result.enhancement_result.status is EnhancementStatus.COMPLETED
    assert result.enhancement_result.input_tokens > 0
    assert result.enhancement_result.output_tokens > 0
    assert len(result.enhancement_result.enhanced_evidence) == 1
    enhanced_text = result.enhancement_result.enhanced_evidence[0].text
    assert "slack-vault-phase-2b-live-ok" in enhanced_text.lower()
    record = result.source_record.path.read_text(encoding="utf-8")
    assert 'extraction_status: "completed"' in record
    assert 'enhancement_status: "completed"' in record
    assert "## Extracted Evidence" in record
    assert "## Enhanced Evidence" in record
    assert "slack-vault-phase-2b-live-ok" in record.lower()


class _FakeTextProvider:
    def __init__(self, response: AITextResponse) -> None:
        self.response = response
        self.requests: list[AITextRequest] = []

    def complete_text(self, request: AITextRequest) -> AITextResponse:
        self.requests.append(request)
        return self.response


def _extraction_result() -> ExtractionResult:
    return ExtractionResult.completed(
        extractor_name="markdown",
        evidence=(
            EvidenceBlock(
                sequence=1,
                text="# Overview\n\nNoisy evidence.",
                location=EvidenceLocation(
                    kind=EvidenceLocationKind.HEADING,
                    file_name="Example Plan.md",
                    heading="Overview",
                ),
            ),
        ),
    )


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
