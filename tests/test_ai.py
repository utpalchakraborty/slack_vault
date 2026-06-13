from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from anthropic.types import Message, MessageParam, ModelParam, TextBlock, Usage
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaTextBlock,
    BetaUsage,
    DeletedFile,
    FileMetadata,
)

from slack_vault.ai import (
    ANTHROPIC_FILES_BETA,
    AITextRequest,
    AIUploadedFile,
    AnthropicAIProvider,
)
from slack_vault.config import Settings


def test_anthropic_provider_sends_text_request_to_messages_api() -> None:
    response = _anthropic_message(text="Harness OK", model="claude-test-model")
    fake_client = _FakeAnthropicClient(response)
    provider = AnthropicAIProvider(
        api_key="sk-ant-test-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=fake_client,
    )

    result = provider.complete_text(
        AITextRequest(
            system_prompt="Use terse responses.",
            user_prompt="Say harness OK.",
            max_output_tokens=12,
            temperature=0,
        )
    )

    assert result.text == "Harness OK"
    assert result.model == "claude-test-model"
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 5
    assert result.output_tokens == 3
    assert fake_client.messages.last_max_tokens == 12
    assert fake_client.messages.last_model == "claude-test-model"
    assert fake_client.messages.last_system == "Use terse responses."
    assert fake_client.messages.last_temperature == 0
    assert fake_client.messages.last_messages == (
        {"role": "user", "content": "Say harness OK."},
    )


def test_anthropic_provider_uploads_file(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source evidence", encoding="utf-8")
    fake_client = _FakeAnthropicClient(
        message_response=_anthropic_message(text="unused", model="claude-test-model"),
        file_upload_response=_anthropic_file_metadata(
            file_id="file_test123",
            filename="source.txt",
        ),
    )
    provider = AnthropicAIProvider(
        api_key="sk-ant-test-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=fake_client,
    )

    uploaded = provider.upload_file(source)

    assert uploaded == AIUploadedFile(
        file_id="file_test123",
        filename="source.txt",
        mime_type="text/plain",
        size_bytes=15,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )
    assert fake_client.beta.files.last_uploaded_file == source


def test_anthropic_provider_sends_uploaded_files_to_beta_messages() -> None:
    fake_client = _FakeAnthropicClient(
        message_response=_anthropic_message(text="unused", model="claude-test-model"),
        beta_message_response=_anthropic_beta_message(
            text="File harness OK",
            model="claude-test-model",
        ),
    )
    provider = AnthropicAIProvider(
        api_key="sk-ant-test-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=fake_client,
    )
    uploaded = AIUploadedFile(
        file_id="file_test123",
        filename="source.txt",
        mime_type="text/plain",
        size_bytes=15,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )

    result = provider.complete_text_with_files(
        AITextRequest(
            system_prompt="Use uploaded files only.",
            user_prompt="Summarize the uploaded file.",
            max_output_tokens=20,
            temperature=0,
        ),
        files=(uploaded,),
    )

    assert result.text == "File harness OK"
    assert result.model == "claude-test-model"
    assert fake_client.beta.messages.last_betas == [ANTHROPIC_FILES_BETA]
    assert fake_client.beta.messages.last_max_tokens == 20
    assert fake_client.beta.messages.last_temperature == 0
    assert fake_client.beta.messages.last_messages == (
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "title": "source.txt",
                    "source": {
                        "type": "file",
                        "file_id": "file_test123",
                    },
                },
                {"type": "text", "text": "Summarize the uploaded file."},
            ],
        },
    )


def test_anthropic_provider_deletes_file() -> None:
    fake_client = _FakeAnthropicClient(
        message_response=_anthropic_message(text="unused", model="claude-test-model"),
        file_delete_response=DeletedFile(id="file_test123", type="file_deleted"),
    )
    provider = AnthropicAIProvider(
        api_key="sk-ant-test-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=fake_client,
    )

    assert provider.delete_file("file_test123") is True
    assert fake_client.beta.files.last_deleted_file_id == "file_test123"


def test_anthropic_provider_from_settings_requires_anthropic_api_key() -> None:
    settings = Settings.from_env({})

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicAIProvider.from_settings(settings)


def test_anthropic_provider_from_settings_uses_configured_model_and_key() -> None:
    settings = Settings.from_env(
        {
            "ANTHROPIC_API_KEY": "sk-ant-test-value",
            "SLACK_VAULT_ANTHROPIC_MODEL": "claude-test-model",
            "SLACK_VAULT_AI_MAX_OUTPUT_TOKENS": "123",
        }
    )

    provider = AnthropicAIProvider.from_settings(settings)

    assert provider.model == "claude-test-model"
    assert provider.max_output_tokens == 123
    assert "sk-ant-test-value" not in repr(provider)


def test_anthropic_live_smoke_test(tmp_path: Path) -> None:
    if os.environ.get("SLACK_VAULT_RUN_LIVE_AI_TESTS") != "1":
        pytest.skip("set SLACK_VAULT_RUN_LIVE_AI_TESTS=1 to run live AI tests")

    provider = AnthropicAIProvider.from_settings(Settings.from_env())
    response = provider.complete_text(
        AITextRequest(
            system_prompt=(
                "Return exactly the requested token. Do not add punctuation or "
                "explanation."
            ),
            user_prompt="Return exactly: slack-vault-live-ok",
            max_output_tokens=16,
            temperature=0,
        )
    )

    assert response.text.strip() == "slack-vault-live-ok"
    assert response.input_tokens > 0
    assert response.output_tokens > 0

    source = tmp_path / "live-file.txt"
    source.write_text("The requested token is slack-vault-file-ok.", encoding="utf-8")
    uploaded = provider.upload_file(source)
    try:
        file_response = provider.complete_text_with_files(
            AITextRequest(
                system_prompt=(
                    "Answer from the uploaded file. Return exactly the requested "
                    "token and nothing else."
                ),
                user_prompt="What token is in the uploaded file?",
                max_output_tokens=16,
                temperature=0,
            ),
            files=(uploaded,),
        )
    finally:
        assert provider.delete_file(uploaded.file_id) is True

    assert file_response.text.strip() == "slack-vault-file-ok"
    assert file_response.input_tokens > 0
    assert file_response.output_tokens > 0


class _FakeMessagesClient:
    def __init__(self, response: Message) -> None:
        self.response = response
        self.last_max_tokens: int | None = None
        self.last_messages: tuple[MessageParam, ...] | None = None
        self.last_model: ModelParam | None = None
        self.last_system: str | None = None
        self.last_temperature: float | None = None

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: ModelParam,
        system: str,
        temperature: float | None = None,
    ) -> Message:
        self.last_max_tokens = max_tokens
        self.last_messages = tuple(messages)
        self.last_model = model
        self.last_system = system
        self.last_temperature = temperature
        return self.response


class _FakeBetaFilesClient:
    def __init__(
        self,
        upload_response: FileMetadata,
        delete_response: DeletedFile,
    ) -> None:
        self.upload_response = upload_response
        self.delete_response = delete_response
        self.last_uploaded_file: Path | None = None
        self.last_deleted_file_id: str | None = None

    def upload(self, *, file: Path) -> FileMetadata:
        self.last_uploaded_file = file
        return self.upload_response

    def delete(self, file_id: str) -> DeletedFile:
        self.last_deleted_file_id = file_id
        return self.delete_response


class _FakeBetaMessagesClient:
    def __init__(self, response: BetaMessage) -> None:
        self.response = response
        self.last_max_tokens: int | None = None
        self.last_messages: tuple[BetaMessageParam, ...] | None = None
        self.last_model: ModelParam | None = None
        self.last_system: str | None = None
        self.last_betas: list[str] | None = None
        self.last_temperature: float | None = None

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[BetaMessageParam],
        model: ModelParam,
        system: str,
        betas: list[str],
        temperature: float | None = None,
    ) -> BetaMessage:
        self.last_max_tokens = max_tokens
        self.last_messages = tuple(messages)
        self.last_model = model
        self.last_system = system
        self.last_betas = betas
        self.last_temperature = temperature
        return self.response


class _FakeBetaClient:
    def __init__(
        self,
        *,
        file_upload_response: FileMetadata,
        file_delete_response: DeletedFile,
        beta_message_response: BetaMessage,
    ) -> None:
        self.files = _FakeBetaFilesClient(
            upload_response=file_upload_response,
            delete_response=file_delete_response,
        )
        self.messages = _FakeBetaMessagesClient(beta_message_response)


class _FakeAnthropicClient:
    def __init__(
        self,
        message_response: Message,
        *,
        file_upload_response: FileMetadata | None = None,
        file_delete_response: DeletedFile | None = None,
        beta_message_response: BetaMessage | None = None,
    ) -> None:
        self.messages = _FakeMessagesClient(message_response)
        self.beta = _FakeBetaClient(
            file_upload_response=file_upload_response
            or _anthropic_file_metadata(
                file_id="file_default",
                filename="default.txt",
            ),
            file_delete_response=file_delete_response
            or DeletedFile(id="file_default", type="file_deleted"),
            beta_message_response=beta_message_response
            or _anthropic_beta_message(text="unused", model="claude-test-model"),
        )


def _anthropic_message(*, text: str, model: str) -> Message:
    return Message(
        id="msg_test",
        content=[
            TextBlock(
                text=text,
                type="text",
            )
        ],
        model=model,
        role="assistant",
        stop_reason="end_turn",
        type="message",
        usage=Usage(input_tokens=5, output_tokens=3),
    )


def _anthropic_beta_message(*, text: str, model: str) -> BetaMessage:
    return BetaMessage(
        id="msg_test",
        content=[
            BetaTextBlock(
                text=text,
                type="text",
            )
        ],
        model=model,
        role="assistant",
        stop_reason="end_turn",
        type="message",
        usage=BetaUsage(input_tokens=7, output_tokens=4),
    )


def _anthropic_file_metadata(*, file_id: str, filename: str) -> FileMetadata:
    return FileMetadata(
        id=file_id,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
        filename=filename,
        mime_type="text/plain",
        size_bytes=15,
        type="file",
    )
