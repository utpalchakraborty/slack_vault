"""AI provider harnesses."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from anthropic.types import (
    CacheControlEphemeralParam,
    Message,
    MessageParam,
    ModelParam,
    TextBlock,
    TextBlockParam,
)
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaMessage,
    BetaMessageParam,
    BetaRequestDocumentBlockParam,
    BetaTextBlock,
    BetaTextBlockParam,
    DeletedFile,
    FileMetadata,
)

from slack_vault.config import AIProvider, AIRetrySettings, Settings

ANTHROPIC_FILES_BETA = "files-api-2025-04-14"
RETRYABLE_AI_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
PromptCacheTTL = Literal["5m", "1h"]
SleepFunction = Callable[[float], None]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIPromptCacheConfig:
    """Provider-independent prompt cache settings for a request."""

    enabled: bool = True
    ttl: PromptCacheTTL = "5m"
    automatic: bool = True
    cache_system_prompt: bool = False
    cache_uploaded_files: bool = False


@dataclass(frozen=True)
class AITextRequest:
    """A provider-independent text completion request."""

    system_prompt: str
    user_prompt: str
    model: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    prompt_cache: AIPromptCacheConfig | None = None


@dataclass(frozen=True)
class AITextResponse:
    """A provider-independent text completion response."""

    text: str
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class AIUploadedFile:
    """Provider-independent metadata for a file uploaded to an AI provider."""

    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class AITextProvider(Protocol):
    """Interface for text-capable AI providers."""

    def complete_text(self, request: AITextRequest) -> AITextResponse:
        """Run a text completion request."""


class AIFileProvider(Protocol):
    """Interface for file-capable AI providers."""

    def upload_file(self, file_path: Path) -> AIUploadedFile:
        """Upload a file for later use in AI requests."""

    def complete_text_with_files(
        self,
        request: AITextRequest,
        files: tuple[AIUploadedFile, ...],
    ) -> AITextResponse:
        """Run a text completion request grounded in uploaded files."""

    def delete_file(self, file_id: str) -> bool:
        """Delete an uploaded provider file."""


@dataclass(frozen=True)
class RetryingAITextProvider:
    """Retry transient failures from an AI text provider."""

    provider: AITextProvider
    retry: AIRetrySettings
    sleep: SleepFunction = time.sleep

    def __post_init__(self) -> None:
        if self.retry.max_attempts < 1:
            raise ValueError("AI retry max_attempts must be at least 1")
        if self.retry.initial_delay_seconds < 0:
            raise ValueError("AI retry initial delay must be non-negative")
        if self.retry.max_delay_seconds < 0:
            raise ValueError("AI retry max delay must be non-negative")
        if self.retry.backoff_multiplier <= 0:
            raise ValueError("AI retry backoff multiplier must be greater than 0")

    def complete_text(self, request: AITextRequest) -> AITextResponse:
        """Run a text completion, retrying transient provider failures."""

        delay_seconds = self.retry.initial_delay_seconds
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                return self.provider.complete_text(request)
            except Exception as exc:
                if attempt >= self.retry.max_attempts or not is_retryable_ai_error(exc):
                    raise

                logger.warning(
                    "Retryable AI provider error attempt=%s max_attempts=%s "
                    "delay_seconds=%s error_type=%s error=%s",
                    attempt,
                    self.retry.max_attempts,
                    delay_seconds,
                    type(exc).__name__,
                    exc,
                )
                if delay_seconds > 0:
                    self.sleep(delay_seconds)
                delay_seconds = _next_retry_delay(
                    delay_seconds,
                    max_delay_seconds=self.retry.max_delay_seconds,
                    backoff_multiplier=self.retry.backoff_multiplier,
                )

        raise RuntimeError("unreachable AI retry loop state")


def is_retryable_ai_error(exc: Exception) -> bool:
    """Return whether an AI provider error is safe to retry."""

    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_AI_STATUS_CODES

    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and status_code in RETRYABLE_AI_STATUS_CODES


def _next_retry_delay(
    delay_seconds: float,
    *,
    max_delay_seconds: float,
    backoff_multiplier: float,
) -> float:
    if max_delay_seconds == 0:
        return 0
    return min(max_delay_seconds, delay_seconds * backoff_multiplier)


class _AnthropicMessagesClient(Protocol):
    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: ModelParam,
        system: str | Iterable[TextBlockParam],
        cache_control: CacheControlEphemeralParam | None = None,
        temperature: float | None = None,
    ) -> object:
        """Create an Anthropic message."""


class _AnthropicBetaFilesClient(Protocol):
    def upload(self, *, file: Path) -> object:
        """Upload a file through the Anthropic beta Files API."""

    def delete(self, file_id: str) -> object:
        """Delete a file through the Anthropic beta Files API."""


class _AnthropicBetaMessagesClient(Protocol):
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
    ) -> object:
        """Create an Anthropic beta message."""


class _AnthropicBetaClient(Protocol):
    @property
    def files(self) -> _AnthropicBetaFilesClient:
        """Return the Anthropic beta files client."""

    @property
    def messages(self) -> _AnthropicBetaMessagesClient:
        """Return the Anthropic beta messages client."""


class _AnthropicClient(Protocol):
    @property
    def messages(self) -> _AnthropicMessagesClient:
        """Return the Anthropic messages client."""

    @property
    def beta(self) -> _AnthropicBetaClient:
        """Return the Anthropic beta client."""


@dataclass(frozen=True)
class AnthropicAIProvider:
    """Anthropic-backed implementation of the AI text provider interface."""

    api_key: str = field(repr=False)
    model: str
    max_output_tokens: int
    prompt_cache: AIPromptCacheConfig | None = None
    client: _AnthropicClient | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicAIProvider:
        """Create an Anthropic provider from resolved application settings."""

        if settings.ai.provider is not AIProvider.ANTHROPIC:
            raise ValueError(
                f"Settings are not configured for Anthropic: "
                f"{settings.ai.provider.value}"
            )
        if settings.ai.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required for Anthropic AI")
        return cls(
            api_key=settings.ai.anthropic_api_key,
            model=settings.ai.model,
            max_output_tokens=settings.ai.max_output_tokens,
        )

    def complete_text(self, request: AITextRequest) -> AITextResponse:
        """Run a text completion through Anthropic Messages."""

        model = request.model or self.model
        max_tokens = request.max_output_tokens or self.max_output_tokens
        prompt_cache = self._prompt_cache_config(request)
        system = _anthropic_system_prompt(request.system_prompt, prompt_cache)
        cache_control = _anthropic_automatic_cache_control(prompt_cache)
        messages: tuple[MessageParam, ...] = (
            {
                "role": "user",
                "content": request.user_prompt,
            },
        )
        client = self._client()

        if request.temperature is None:
            if cache_control is None:
                message = cast(
                    Message,
                    client.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                    ),
                )
            else:
                message = cast(
                    Message,
                    client.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        cache_control=cache_control,
                    ),
                )
        else:
            if cache_control is None:
                message = cast(
                    Message,
                    client.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        temperature=request.temperature,
                    ),
                )
            else:
                message = cast(
                    Message,
                    client.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        cache_control=cache_control,
                        temperature=request.temperature,
                    ),
                )

        return AITextResponse(
            text=_message_text(message),
            model=str(message.model),
            stop_reason=None
            if message.stop_reason is None
            else str(message.stop_reason),
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_creation_input_tokens=message.usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=message.usage.cache_read_input_tokens or 0,
        )

    def upload_file(self, file_path: Path) -> AIUploadedFile:
        """Upload a file to Anthropic's beta Files API."""

        metadata = cast(
            FileMetadata,
            self._client().beta.files.upload(file=file_path),
        )
        return AIUploadedFile(
            file_id=metadata.id,
            filename=metadata.filename,
            mime_type=metadata.mime_type,
            size_bytes=metadata.size_bytes,
            created_at=metadata.created_at,
        )

    def complete_text_with_files(
        self,
        request: AITextRequest,
        files: tuple[AIUploadedFile, ...],
    ) -> AITextResponse:
        """Run a text completion grounded in uploaded Anthropic files."""

        if not files:
            return self.complete_text(request)

        model = request.model or self.model
        max_tokens = request.max_output_tokens or self.max_output_tokens
        prompt_cache = self._prompt_cache_config(request)
        system = _anthropic_beta_system_prompt(request.system_prompt, prompt_cache)
        cache_control = _anthropic_beta_automatic_cache_control(prompt_cache)
        content: list[BetaRequestDocumentBlockParam | BetaTextBlockParam] = [
            _uploaded_file_document_block(file, prompt_cache) for file in files
        ]
        content.append({"type": "text", "text": request.user_prompt})
        messages: tuple[BetaMessageParam, ...] = ({"role": "user", "content": content},)
        client = self._client()

        if request.temperature is None:
            if cache_control is None:
                message = cast(
                    BetaMessage,
                    client.beta.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        betas=[ANTHROPIC_FILES_BETA],
                    ),
                )
            else:
                message = cast(
                    BetaMessage,
                    client.beta.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        betas=[ANTHROPIC_FILES_BETA],
                        cache_control=cache_control,
                    ),
                )
        else:
            if cache_control is None:
                message = cast(
                    BetaMessage,
                    client.beta.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        betas=[ANTHROPIC_FILES_BETA],
                        temperature=request.temperature,
                    ),
                )
            else:
                message = cast(
                    BetaMessage,
                    client.beta.messages.create(
                        max_tokens=max_tokens,
                        messages=messages,
                        model=model,
                        system=system,
                        betas=[ANTHROPIC_FILES_BETA],
                        cache_control=cache_control,
                        temperature=request.temperature,
                    ),
                )

        return AITextResponse(
            text=_beta_message_text(message),
            model=str(message.model),
            stop_reason=None
            if message.stop_reason is None
            else str(message.stop_reason),
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_creation_input_tokens=message.usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=message.usage.cache_read_input_tokens or 0,
        )

    def delete_file(self, file_id: str) -> bool:
        """Delete a file from Anthropic's beta Files API."""

        deleted = cast(DeletedFile, self._client().beta.files.delete(file_id))
        return deleted.id == file_id

    def _client(self) -> _AnthropicClient:
        if self.client is not None:
            return self.client
        return cast(_AnthropicClient, Anthropic(api_key=self.api_key))

    def _prompt_cache_config(
        self,
        request: AITextRequest,
    ) -> AIPromptCacheConfig | None:
        if request.prompt_cache is not None:
            return request.prompt_cache
        return self.prompt_cache


def _message_text(message: Message) -> str:
    return "\n".join(
        block.text for block in message.content if isinstance(block, TextBlock)
    ).strip()


def _beta_message_text(message: BetaMessage) -> str:
    return "\n".join(
        block.text for block in message.content if isinstance(block, BetaTextBlock)
    ).strip()


def _uploaded_file_document_block(
    uploaded_file: AIUploadedFile,
    prompt_cache: AIPromptCacheConfig | None,
) -> BetaRequestDocumentBlockParam:
    block: BetaRequestDocumentBlockParam = {
        "type": "document",
        "title": uploaded_file.filename,
        "source": {
            "type": "file",
            "file_id": uploaded_file.file_id,
        },
    }
    cache_control = _anthropic_beta_uploaded_file_cache_control(prompt_cache)
    if cache_control is not None:
        block["cache_control"] = cache_control
    return block


def _anthropic_system_prompt(
    system_prompt: str,
    prompt_cache: AIPromptCacheConfig | None,
) -> str | tuple[TextBlockParam, ...]:
    cache_control = _anthropic_explicit_cache_control(prompt_cache)
    if cache_control is None:
        return system_prompt
    return (
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": cache_control,
        },
    )


def _anthropic_beta_system_prompt(
    system_prompt: str,
    prompt_cache: AIPromptCacheConfig | None,
) -> str | tuple[BetaTextBlockParam, ...]:
    cache_control = _anthropic_beta_system_cache_control(prompt_cache)
    if cache_control is None:
        return system_prompt
    return (
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": cache_control,
        },
    )


def _anthropic_automatic_cache_control(
    prompt_cache: AIPromptCacheConfig | None,
) -> CacheControlEphemeralParam | None:
    if prompt_cache is None or not prompt_cache.enabled or not prompt_cache.automatic:
        return None
    return _anthropic_cache_control(prompt_cache)


def _anthropic_beta_automatic_cache_control(
    prompt_cache: AIPromptCacheConfig | None,
) -> BetaCacheControlEphemeralParam | None:
    if prompt_cache is None or not prompt_cache.enabled or not prompt_cache.automatic:
        return None
    return _anthropic_beta_cache_control(prompt_cache)


def _anthropic_explicit_cache_control(
    prompt_cache: AIPromptCacheConfig | None,
) -> CacheControlEphemeralParam | None:
    if (
        prompt_cache is None
        or not prompt_cache.enabled
        or not prompt_cache.cache_system_prompt
    ):
        return None
    return _anthropic_cache_control(prompt_cache)


def _anthropic_beta_system_cache_control(
    prompt_cache: AIPromptCacheConfig | None,
) -> BetaCacheControlEphemeralParam | None:
    if (
        prompt_cache is None
        or not prompt_cache.enabled
        or not prompt_cache.cache_system_prompt
    ):
        return None
    return _anthropic_beta_cache_control(prompt_cache)


def _anthropic_beta_uploaded_file_cache_control(
    prompt_cache: AIPromptCacheConfig | None,
) -> BetaCacheControlEphemeralParam | None:
    if (
        prompt_cache is None
        or not prompt_cache.enabled
        or not prompt_cache.cache_uploaded_files
    ):
        return None
    return _anthropic_beta_cache_control(prompt_cache)


def _anthropic_cache_control(
    prompt_cache: AIPromptCacheConfig,
) -> CacheControlEphemeralParam:
    cache_control: CacheControlEphemeralParam = {"type": "ephemeral"}
    if prompt_cache.ttl != "5m":
        cache_control["ttl"] = prompt_cache.ttl
    return cache_control


def _anthropic_beta_cache_control(
    prompt_cache: AIPromptCacheConfig,
) -> BetaCacheControlEphemeralParam:
    cache_control: BetaCacheControlEphemeralParam = {"type": "ephemeral"}
    if prompt_cache.ttl != "5m":
        cache_control["ttl"] = prompt_cache.ttl
    return cache_control
