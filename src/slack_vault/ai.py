"""AI provider harnesses."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from anthropic import Anthropic
from anthropic.types import Message, MessageParam, ModelParam, TextBlock
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaRequestDocumentBlockParam,
    BetaTextBlock,
    BetaTextBlockParam,
    DeletedFile,
    FileMetadata,
)

from slack_vault.config import AIProvider, Settings

ANTHROPIC_FILES_BETA = "files-api-2025-04-14"


@dataclass(frozen=True)
class AITextRequest:
    """A provider-independent text completion request."""

    system_prompt: str
    user_prompt: str
    model: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class AITextResponse:
    """A provider-independent text completion response."""

    text: str
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int


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


class _AnthropicMessagesClient(Protocol):
    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: ModelParam,
        system: str,
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
        system: str,
        betas: list[str],
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
        messages: tuple[MessageParam, ...] = (
            {
                "role": "user",
                "content": request.user_prompt,
            },
        )
        client = self._client()

        if request.temperature is None:
            message = cast(
                Message,
                client.messages.create(
                    max_tokens=max_tokens,
                    messages=messages,
                    model=model,
                    system=request.system_prompt,
                ),
            )
        else:
            message = cast(
                Message,
                client.messages.create(
                    max_tokens=max_tokens,
                    messages=messages,
                    model=model,
                    system=request.system_prompt,
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
        content: list[BetaRequestDocumentBlockParam | BetaTextBlockParam] = [
            _uploaded_file_document_block(file) for file in files
        ]
        content.append({"type": "text", "text": request.user_prompt})
        messages: tuple[BetaMessageParam, ...] = ({"role": "user", "content": content},)
        client = self._client()

        if request.temperature is None:
            message = cast(
                BetaMessage,
                client.beta.messages.create(
                    max_tokens=max_tokens,
                    messages=messages,
                    model=model,
                    system=request.system_prompt,
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
                    system=request.system_prompt,
                    betas=[ANTHROPIC_FILES_BETA],
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
        )

    def delete_file(self, file_id: str) -> bool:
        """Delete a file from Anthropic's beta Files API."""

        deleted = cast(DeletedFile, self._client().beta.files.delete(file_id))
        return deleted.id == file_id

    def _client(self) -> _AnthropicClient:
        if self.client is not None:
            return self.client
        return cast(_AnthropicClient, Anthropic(api_key=self.api_key))


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
) -> BetaRequestDocumentBlockParam:
    return {
        "type": "document",
        "title": uploaded_file.filename,
        "source": {
            "type": "file",
            "file_id": uploaded_file.file_id,
        },
    }
