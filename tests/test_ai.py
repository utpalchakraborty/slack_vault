from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anthropic.types import (
    CacheControlEphemeralParam,
    Message,
    MessageParam,
    ModelParam,
    TextBlock,
    TextBlockParam,
    Usage,
)
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaUsage,
    DeletedFile,
    FileMetadata,
)

from slack_vault.ai import (
    ANTHROPIC_FILES_BETA,
    AIInteractionLogger,
    AIPromptCacheConfig,
    AITextRequest,
    AITextResponse,
    AIUploadedFile,
    AnthropicAIProvider,
    RetryingAITextProvider,
)
from slack_vault.config import AIRetrySettings, Settings


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
    assert result.cache_creation_input_tokens == 0
    assert result.cache_read_input_tokens == 0
    assert fake_client.messages.last_max_tokens == 12
    assert fake_client.messages.last_model == "claude-test-model"
    assert fake_client.messages.last_system == "Use terse responses."
    assert fake_client.messages.last_cache_control is None
    assert fake_client.messages.last_temperature == 0
    assert fake_client.messages.last_messages == (
        {"role": "user", "content": "Say harness OK."},
    )


def test_anthropic_provider_applies_prompt_cache_to_text_request() -> None:
    response = _anthropic_message(
        text="Harness OK",
        model="claude-test-model",
        cache_creation_input_tokens=1200,
        cache_read_input_tokens=300,
    )
    fake_client = _FakeAnthropicClient(response)
    prompt_cache = AIPromptCacheConfig(
        ttl="1h",
        cache_system_prompt=True,
    )
    provider = AnthropicAIProvider(
        api_key="sk-ant-test-value",
        model="claude-test-model",
        max_output_tokens=64,
        prompt_cache=prompt_cache,
        client=fake_client,
    )

    result = provider.complete_text(
        AITextRequest(
            system_prompt="Stable enhancement rules.",
            user_prompt="Enhance this evidence.",
            max_output_tokens=12,
        )
    )

    cache_control = {"type": "ephemeral", "ttl": "1h"}
    assert result.cache_creation_input_tokens == 1200
    assert result.cache_read_input_tokens == 300
    assert fake_client.messages.last_cache_control == cache_control
    assert fake_client.messages.last_system == (
        {
            "type": "text",
            "text": "Stable enhancement rules.",
            "cache_control": cache_control,
        },
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
    assert fake_client.beta.messages.last_cache_control is None
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


def test_anthropic_provider_applies_prompt_cache_to_file_request() -> None:
    fake_client = _FakeAnthropicClient(
        message_response=_anthropic_message(text="unused", model="claude-test-model"),
        beta_message_response=_anthropic_beta_message(
            text="File harness OK",
            model="claude-test-model",
            cache_creation_input_tokens=2400,
            cache_read_input_tokens=600,
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
            prompt_cache=AIPromptCacheConfig(cache_uploaded_files=True),
        ),
        files=(uploaded,),
    )

    cache_control = {"type": "ephemeral"}
    assert result.cache_creation_input_tokens == 2400
    assert result.cache_read_input_tokens == 600
    assert fake_client.beta.messages.last_cache_control == cache_control
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
                    "cache_control": cache_control,
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
    assert provider.interaction_logger is not None
    assert provider.interaction_logger.path == Path(".data/logs/ai-interactions.jsonl")
    assert provider.interaction_logger.backup_count == 14
    assert "sk-ant-test-value" not in repr(provider)


def test_anthropic_provider_logs_text_request_and_response(tmp_path: Path) -> None:
    log_path = tmp_path / "ai-interactions.jsonl"
    fake_client = _FakeAnthropicClient(
        _anthropic_message(text="Logged OK", model="claude-test-model")
    )
    provider = AnthropicAIProvider(
        api_key="sk-ant-secret-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=fake_client,
        interaction_logger=AIInteractionLogger(log_path),
    )

    provider.complete_text(
        AITextRequest(
            system_prompt="Use logged responses.",
            user_prompt="Say logged OK.",
            max_output_tokens=12,
            temperature=0,
            prompt_cache=AIPromptCacheConfig(
                ttl="1h",
                cache_system_prompt=True,
            ),
        )
    )

    records = _jsonl_records(log_path)
    assert [record["event"] for record in records] == ["request", "response"]
    assert records[0]["interaction_id"] == records[1]["interaction_id"]
    assert records[0]["provider"] == "anthropic"
    assert records[0]["method"] == "complete_text"
    assert records[0]["model"] == "claude-test-model"
    assert records[0]["system_prompt"] == "Use logged responses."
    assert records[0]["user_prompt"] == "Say logged OK."
    assert records[0]["temperature"] == 0
    assert records[0]["max_output_tokens"] == 12
    assert records[0]["prompt_cache"] == {
        "automatic": True,
        "cache_system_prompt": True,
        "cache_uploaded_files": False,
        "enabled": True,
        "ttl": "1h",
    }
    assert records[0]["files"] == []
    assert records[1]["text"] == "Logged OK"
    assert records[1]["input_tokens"] == 5
    assert records[1]["output_tokens"] == 3
    assert "sk-ant-secret-value" not in log_path.read_text(encoding="utf-8")


def test_ai_interaction_logger_rotates_and_gzips_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "ai-interactions.jsonl"
    interaction_logger = AIInteractionLogger(log_path, backup_count=1)
    interaction_id = interaction_logger.log_request(
        provider="anthropic",
        method="complete_text",
        model="claude-test-model",
        max_output_tokens=12,
        request=AITextRequest(
            system_prompt="Old request.",
            user_prompt="Say old.",
        ),
        prompt_cache=None,
    )
    old_timestamp = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(log_path, (old_timestamp, old_timestamp))

    interaction_logger.log_response(
        interaction_id=interaction_id,
        provider="anthropic",
        method="complete_text",
        response=_text_response("new response"),
    )

    rotated_logs = sorted(log_path.parent.glob("ai-interactions.jsonl.*.gz"))
    assert len(rotated_logs) == 1
    assert _jsonl_records(log_path)[0]["event"] == "response"
    with gzip.open(rotated_logs[0], "rt", encoding="utf-8") as rotated_file:
        rotated_records = [
            json.loads(line) for line in rotated_file.read().splitlines() if line
        ]
    assert rotated_records[0]["event"] == "request"
    assert rotated_records[0]["system_prompt"] == "Old request."


def test_anthropic_provider_logs_file_request_and_response(tmp_path: Path) -> None:
    log_path = tmp_path / "ai-interactions.jsonl"
    fake_client = _FakeAnthropicClient(
        message_response=_anthropic_message(text="unused", model="claude-test-model"),
        beta_message_response=_anthropic_beta_message(
            text="File logged OK",
            model="claude-test-model",
        ),
    )
    provider = AnthropicAIProvider(
        api_key="sk-ant-secret-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=fake_client,
        interaction_logger=AIInteractionLogger(log_path),
    )
    uploaded = AIUploadedFile(
        file_id="file_test123",
        filename="source.txt",
        mime_type="text/plain",
        size_bytes=15,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )

    provider.complete_text_with_files(
        AITextRequest(
            system_prompt="Use uploaded files only.",
            user_prompt="Summarize the uploaded file.",
            max_output_tokens=20,
        ),
        files=(uploaded,),
    )

    records = _jsonl_records(log_path)
    assert [record["event"] for record in records] == ["request", "response"]
    assert records[0]["method"] == "complete_text_with_files"
    assert records[0]["files"] == [
        {
            "file_id": "file_test123",
            "filename": "source.txt",
            "mime_type": "text/plain",
            "size_bytes": 15,
            "created_at": "2026-06-13T12:00:00+00:00",
        }
    ]
    assert records[1]["text"] == "File logged OK"


def test_anthropic_provider_logs_text_request_errors(tmp_path: Path) -> None:
    log_path = tmp_path / "ai-interactions.jsonl"
    provider = AnthropicAIProvider(
        api_key="sk-ant-secret-value",
        model="claude-test-model",
        max_output_tokens=64,
        client=_FailingAnthropicClient(RuntimeError("provider failed")),
        interaction_logger=AIInteractionLogger(log_path),
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        provider.complete_text(
            AITextRequest(
                system_prompt="Use logged responses.",
                user_prompt="Raise an error.",
                max_output_tokens=12,
            )
        )

    records = _jsonl_records(log_path)
    assert [record["event"] for record in records] == ["request", "error"]
    assert records[0]["interaction_id"] == records[1]["interaction_id"]
    assert records[1]["method"] == "complete_text"
    assert records[1]["error_type"] == "RuntimeError"
    assert records[1]["error_message"] == "provider failed"


def test_retrying_ai_provider_retries_retryable_status_errors() -> None:
    provider = _FlakyTextProvider(
        failures=[
            _FakeStatusError(429, "rate limited"),
            _FakeStatusError(503, "service unavailable"),
        ],
        response=_text_response("retry ok"),
    )
    sleeps: list[float] = []
    retrying_provider = RetryingAITextProvider(
        provider,
        retry=AIRetrySettings(
            max_attempts=3,
            initial_delay_seconds=1.5,
            max_delay_seconds=2.0,
            backoff_multiplier=2.0,
        ),
        sleep=sleeps.append,
    )

    result = retrying_provider.complete_text(
        AITextRequest(system_prompt="system", user_prompt="user")
    )

    assert result.text == "retry ok"
    assert provider.calls == 3
    assert sleeps == [1.5, 2.0]


def test_retrying_ai_provider_raises_after_attempts_are_exhausted() -> None:
    provider = _FlakyTextProvider(
        failures=[
            _FakeStatusError(429, "rate limited"),
            _FakeStatusError(429, "still limited"),
            _FakeStatusError(429, "still limited again"),
        ],
        response=_text_response("unused"),
    )
    sleeps: list[float] = []
    retrying_provider = RetryingAITextProvider(
        provider,
        retry=AIRetrySettings(
            max_attempts=3,
            initial_delay_seconds=1.0,
            max_delay_seconds=10.0,
            backoff_multiplier=2.0,
        ),
        sleep=sleeps.append,
    )

    with pytest.raises(_FakeStatusError, match="still limited again"):
        retrying_provider.complete_text(
            AITextRequest(system_prompt="system", user_prompt="user")
        )

    assert provider.calls == 3
    assert sleeps == [1.0, 2.0]


def test_retrying_ai_provider_does_not_retry_non_retryable_errors() -> None:
    provider = _FlakyTextProvider(
        failures=[_FakeStatusError(400, "bad request")],
        response=_text_response("unused"),
    )
    sleeps: list[float] = []
    retrying_provider = RetryingAITextProvider(
        provider,
        retry=AIRetrySettings(
            max_attempts=3,
            initial_delay_seconds=1.0,
            max_delay_seconds=10.0,
            backoff_multiplier=2.0,
        ),
        sleep=sleeps.append,
    )

    with pytest.raises(_FakeStatusError, match="bad request"):
        retrying_provider.complete_text(
            AITextRequest(system_prompt="system", user_prompt="user")
        )

    assert provider.calls == 1
    assert sleeps == []


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
        self.last_system: str | tuple[TextBlockParam, ...] | None = None
        self.last_cache_control: CacheControlEphemeralParam | None = None
        self.last_temperature: float | None = None

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: ModelParam,
        system: str | Iterable[TextBlockParam],
        cache_control: CacheControlEphemeralParam | None = None,
        temperature: float | None = None,
    ) -> Message:
        self.last_max_tokens = max_tokens
        self.last_messages = tuple(messages)
        self.last_model = model
        self.last_system = tuple(system) if not isinstance(system, str) else system
        self.last_cache_control = cache_control
        self.last_temperature = temperature
        return self.response


class _FailingMessagesClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: ModelParam,
        system: str | Iterable[TextBlockParam],
        cache_control: CacheControlEphemeralParam | None = None,
        temperature: float | None = None,
    ) -> Message:
        raise self.exc


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
        self.last_system: str | tuple[BetaTextBlockParam, ...] | None = None
        self.last_betas: list[str] | None = None
        self.last_cache_control: BetaCacheControlEphemeralParam | None = None
        self.last_temperature: float | None = None

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[BetaMessageParam],
        model: ModelParam,
        system: str | Iterable[BetaTextBlockParam],
        betas: list[str],
        cache_control: BetaCacheControlEphemeralParam | None = None,
        temperature: float | None = None,
    ) -> BetaMessage:
        self.last_max_tokens = max_tokens
        self.last_messages = tuple(messages)
        self.last_model = model
        self.last_system = tuple(system) if not isinstance(system, str) else system
        self.last_betas = betas
        self.last_cache_control = cache_control
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


class _FailingAnthropicClient:
    def __init__(self, exc: Exception) -> None:
        self.messages = _FailingMessagesClient(exc)
        self.beta = _FakeBetaClient(
            file_upload_response=_anthropic_file_metadata(
                file_id="file_default",
                filename="default.txt",
            ),
            file_delete_response=DeletedFile(id="file_default", type="file_deleted"),
            beta_message_response=_anthropic_beta_message(
                text="unused",
                model="claude-test-model",
            ),
        )


class _FlakyTextProvider:
    def __init__(
        self,
        *,
        failures: list[Exception],
        response: AITextResponse,
    ) -> None:
        self.failures = failures
        self.response = response
        self.calls = 0

    def complete_text(self, request: AITextRequest) -> AITextResponse:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.response


class _FakeStatusError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


def _text_response(text: str) -> AITextResponse:
    return AITextResponse(
        text=text,
        model="fake-model",
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
    )


def _jsonl_records(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _anthropic_message(
    *,
    text: str,
    model: str,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> Message:
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
        usage=Usage(
            input_tokens=5,
            output_tokens=3,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
    )


def _anthropic_beta_message(
    *,
    text: str,
    model: str,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> BetaMessage:
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
        usage=BetaUsage(
            input_tokens=7,
            output_tokens=4,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
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
