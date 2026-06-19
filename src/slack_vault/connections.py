"""Vault connection result models and Git diff validation."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkPluginConfig,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
    query,
)

from slack_vault.config import Settings
from slack_vault.source_registry import source_record_path

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_CONNECTION_PATHS = (
    Path("10 Knowledge"),
    Path("20 Sources/sources"),
    Path("30 Maps"),
)
DEFAULT_PROTECTED_CONNECTION_PATHS = (
    Path(".git"),
    Path(".obsidian"),
    Path("90 System/agent-skills"),
)
_WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
_FRONTMATTER_BOUNDARY = "---\n"
AgentQuery = Callable[..., AsyncIterator[Message]]


class ConnectionStatus(StrEnum):
    """Connection pipeline status values."""

    NOT_REQUESTED = "not_requested"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    AGENT_FAILED = "agent_failed"
    VALIDATION_FAILED = "validation_failed"
    COMMIT_SKIPPED = "commit_skipped"
    FAILED = "failed"


class VaultConnectionError(RuntimeError):
    """Raised when vault connection orchestration cannot proceed."""


@dataclass(frozen=True)
class VaultConnectionResult:
    """Result of connecting one imported source to the existing vault graph."""

    status: ConnectionStatus
    source_id: str
    primary_note_path: Path | None = None
    touched_paths: tuple[Path, ...] = ()
    validation_errors: tuple[str, ...] = ()
    agent_summary: str | None = None


@dataclass(frozen=True)
class VaultDiffRename:
    """One Git rename reported in the vault diff."""

    old_path: Path
    new_path: Path


@dataclass(frozen=True)
class VaultDiffInspection:
    """Inspected Git worktree changes after an agent connection run."""

    touched_paths: tuple[Path, ...]
    changed_paths: tuple[Path, ...]
    added_paths: tuple[Path, ...]
    deleted_paths: tuple[Path, ...]
    renamed_paths: tuple[VaultDiffRename, ...]
    diff_stat: str
    changed_line_count: int


@dataclass(frozen=True)
class VaultDiffValidationConfig:
    """Configuration for validating an agent-produced vault diff."""

    source_id: str
    primary_note_path: Path | None
    max_touched_paths: int = 12
    max_changed_lines: int = 400
    allowed_paths: tuple[Path, ...] = DEFAULT_ALLOWED_CONNECTION_PATHS
    protected_paths: tuple[Path, ...] = DEFAULT_PROTECTED_CONNECTION_PATHS


@dataclass(frozen=True)
class VaultDiffValidationResult:
    """Result of validating vault changes before commit."""

    ok: bool
    stageable_paths: tuple[Path, ...]
    errors: tuple[str, ...]


class VaultConnector(Protocol):
    """Interface for post-synthesis vault connection."""

    def connect(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_record_path: Path,
        primary_note_path: Path | None,
    ) -> VaultConnectionResult:
        """Connect a newly imported source to existing vault notes."""
        ...


@dataclass(frozen=True)
class ClaudeAgentVaultConnector:
    """Claude Agent SDK backed vault connector."""

    obsidian_skills_path: Path
    custom_skills_path: Path
    max_turns: int = 20
    max_touched_paths: int = 12
    max_changed_lines: int = 400
    model: str | None = None
    anthropic_api_key: str | None = None
    query_fn: AgentQuery = query

    @classmethod
    def from_settings(cls, settings: Settings) -> ClaudeAgentVaultConnector:
        """Build a live connector from application settings."""

        return cls(
            obsidian_skills_path=settings.resolved_obsidian_skills_path,
            custom_skills_path=settings.resolved_custom_skills_path,
            max_turns=settings.connection.max_turns,
            max_touched_paths=settings.connection.max_touched_paths,
            max_changed_lines=settings.connection.max_changed_lines,
            model=settings.ai.model,
            anthropic_api_key=settings.ai.anthropic_api_key,
        )

    def connect(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_record_path: Path,
        primary_note_path: Path | None,
    ) -> VaultConnectionResult:
        """Run the connection agent and validate its vault diff."""

        if primary_note_path is None:
            return VaultConnectionResult(
                status=ConnectionStatus.SKIPPED,
                source_id=source_id,
                validation_errors=(
                    "Connection requires a synthesized primary knowledge note.",
                ),
            )

        before = inspect_vault_diff(vault_path)
        prompt = self.build_prompt(
            vault_path,
            source_id=source_id,
            source_record_path=source_record_path,
            primary_note_path=primary_note_path,
        )
        try:
            agent_result = asyncio.run(
                self._run_agent(
                    prompt,
                    vault_path=vault_path,
                )
            )
        except Exception as exc:
            logger.exception("Vault connection agent failed source_id=%s", source_id)
            return VaultConnectionResult(
                status=ConnectionStatus.AGENT_FAILED,
                source_id=source_id,
                primary_note_path=primary_note_path,
                validation_errors=(str(exc),),
            )

        after = inspect_vault_diff(vault_path)
        validation = validate_connection_diff(
            vault_path,
            after,
            VaultDiffValidationConfig(
                source_id=source_id,
                primary_note_path=primary_note_path,
                max_touched_paths=self.max_touched_paths,
                max_changed_lines=self.max_changed_lines,
            ),
        )
        if agent_result.is_error:
            return VaultConnectionResult(
                status=ConnectionStatus.AGENT_FAILED,
                source_id=source_id,
                primary_note_path=primary_note_path,
                touched_paths=_agent_touched_paths(before, after),
                validation_errors=tuple(agent_result.errors),
                agent_summary=agent_result.summary,
            )
        if not validation.ok:
            return VaultConnectionResult(
                status=ConnectionStatus.VALIDATION_FAILED,
                source_id=source_id,
                primary_note_path=primary_note_path,
                touched_paths=_agent_touched_paths(before, after),
                validation_errors=validation.errors,
                agent_summary=agent_result.summary,
            )
        return VaultConnectionResult(
            status=ConnectionStatus.COMPLETED,
            source_id=source_id,
            primary_note_path=primary_note_path,
            touched_paths=_agent_touched_paths(before, after),
            agent_summary=agent_result.summary,
        )

    def build_prompt(
        self,
        vault_path: Path,
        *,
        source_id: str,
        source_record_path: Path,
        primary_note_path: Path,
    ) -> str:
        """Build the connection-agent prompt."""

        return "\n".join(
            [
                "Connect this imported Slack Vault document into the Obsidian "
                "vault graph.",
                "",
                f"Vault path: {vault_path}",
                f"Source ID: {source_id}",
                "Source record path: "
                f"{_display_vault_path(vault_path, source_record_path)}",
                "Primary knowledge note path: "
                f"{_display_vault_path(vault_path, primary_note_path)}",
                "",
                "Use the slack-vault:connect-imported-document workflow.",
                "Use upstream Obsidian skills when useful for Markdown and "
                "Obsidian CLI behavior.",
                "",
                "Allowed edit folders:",
                "- 10 Knowledge/**/*.md",
                "- 20 Sources/sources/**/*.md",
                "- 30 Maps/**/*.md",
                "",
                "Do not run Git commands. Do not edit .obsidian/, .git/, "
                "archives, logs, SQLite databases, Slack downloads, original "
                "source files, or 90 System/agent-skills/**.",
                "Preserve source citations and the ## Sources section.",
                "Prefer sparse, useful links over broad keyword linking.",
                "",
                "Finish with Edited files, Connections added, Skipped "
                "candidates, and Validation notes.",
            ]
        )

    async def _run_agent(
        self,
        prompt: str,
        *,
        vault_path: Path,
    ) -> _AgentResult:
        options = ClaudeAgentOptions(
            tools=["Read", "Edit", "MultiEdit", "Write", "Bash", "Skill"],
            cwd=vault_path,
            add_dirs=[str(vault_path)],
            max_turns=self.max_turns,
            model=self.model,
            mcp_servers={},
            strict_mcp_config=True,
            env={}
            if self.anthropic_api_key is None
            else {"ANTHROPIC_API_KEY": self.anthropic_api_key},
            permission_mode="acceptEdits",
            setting_sources=[],
            skills="all",
            plugins=[
                SdkPluginConfig(type="local", path=str(self.obsidian_skills_path)),
                SdkPluginConfig(type="local", path=str(self.custom_skills_path)),
            ],
            can_use_tool=lambda tool_name, tool_input, context: _can_use_agent_tool(
                vault_path,
                tool_name,
                tool_input,
                context,
            ),
        )
        assistant_text: list[str] = []
        result_summary: str | None = None
        errors: list[str] = []
        is_error = False
        try:
            async for message in self.query_fn(
                prompt=_prompt_stream(prompt), options=options
            ):
                if isinstance(message, AssistantMessage):
                    assistant_text.extend(_assistant_text(message))
                if isinstance(message, ResultMessage):
                    result_summary = message.result
                    is_error = message.is_error
                    if message.errors:
                        errors.extend(str(error) for error in message.errors)
                    if message.api_error_status is not None:
                        errors.append(f"API error status: {message.api_error_status}")
        except Exception as exc:
            if not errors:
                errors.append(str(exc))
            is_error = True
        return _AgentResult(
            summary=result_summary or "\n".join(assistant_text).strip() or None,
            is_error=is_error,
            errors=tuple(errors)
            if errors
            else ("Agent run failed.",)
            if is_error
            else (),
        )


async def _prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }


def inspect_vault_diff(vault_path: Path) -> VaultDiffInspection:
    """Inspect current Git worktree changes in an Obsidian vault."""

    worktree = _ensure_git_repository(vault_path)
    status = _git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    parsed_status = _parse_git_status(status.stdout)
    diff_stat = _combined_git_output(
        worktree,
        (("diff", "--stat"), ("diff", "--cached", "--stat")),
    ).strip()
    changed_line_count = _changed_line_count(
        worktree,
        added_paths=parsed_status.added_paths,
    )
    logger.info(
        "Inspected vault connection diff worktree=%s touched_paths=%s changed_lines=%s",
        worktree,
        len(parsed_status.touched_paths),
        changed_line_count,
    )
    return VaultDiffInspection(
        touched_paths=parsed_status.touched_paths,
        changed_paths=parsed_status.changed_paths,
        added_paths=parsed_status.added_paths,
        deleted_paths=parsed_status.deleted_paths,
        renamed_paths=parsed_status.renamed_paths,
        diff_stat=diff_stat,
        changed_line_count=changed_line_count,
    )


def validate_connection_diff(
    vault_path: Path,
    inspection: VaultDiffInspection,
    config: VaultDiffValidationConfig,
) -> VaultDiffValidationResult:
    """Validate agent-written vault changes before staging or committing them."""

    worktree = _ensure_git_repository(vault_path)
    errors: list[str] = []
    if not inspection.touched_paths:
        errors.append("No vault changes were produced by the connection agent.")
    if len(inspection.touched_paths) > config.max_touched_paths:
        errors.append(
            "Connection touched too many paths: "
            f"{len(inspection.touched_paths)} > {config.max_touched_paths}."
        )
    if inspection.changed_line_count > config.max_changed_lines:
        errors.append(
            "Connection changed too many lines: "
            f"{inspection.changed_line_count} > {config.max_changed_lines}."
        )
    for deleted_path in inspection.deleted_paths:
        errors.append(f"Connection deleted a path: {deleted_path.as_posix()}.")
    for rename in inspection.renamed_paths:
        errors.append(
            "Connection renamed a path: "
            f"{rename.old_path.as_posix()} -> {rename.new_path.as_posix()}."
        )

    stageable_paths: list[Path] = []
    for path in inspection.touched_paths:
        errors.extend(
            _validate_touched_path(
                path,
                allowed_paths=config.allowed_paths,
                protected_paths=config.protected_paths,
            )
        )
        if path not in inspection.deleted_paths:
            stageable_paths.append(path)

    errors.extend(
        _validate_source_artifacts(
            worktree,
            source_id=config.source_id,
            primary_note_path=config.primary_note_path,
        )
    )
    errors.extend(_validate_wikilinks(worktree, inspection.touched_paths))

    return VaultDiffValidationResult(
        ok=not errors,
        stageable_paths=() if errors else tuple(_dedupe_paths(stageable_paths)),
        errors=tuple(errors),
    )


@dataclass(frozen=True)
class _AgentResult:
    summary: str | None
    is_error: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ParsedGitStatus:
    touched_paths: tuple[Path, ...]
    changed_paths: tuple[Path, ...]
    added_paths: tuple[Path, ...]
    deleted_paths: tuple[Path, ...]
    renamed_paths: tuple[VaultDiffRename, ...]


def _parse_git_status(stdout: str) -> _ParsedGitStatus:
    touched_paths: list[Path] = []
    changed_paths: list[Path] = []
    added_paths: list[Path] = []
    deleted_paths: list[Path] = []
    renamed_paths: list[VaultDiffRename] = []

    entries = [entry for entry in stdout.split("\0") if entry]
    index = 0
    while index < len(entries):
        entry = entries[index]
        status = entry[:2]
        raw_path = entry[3:]
        if status == "??":
            path = Path(raw_path)
            touched_paths.append(path)
            added_paths.append(path)
            index += 1
            continue
        if "R" in status and index + 1 < len(entries):
            # In porcelain v1 -z output, rename order is new path then old path.
            rename = VaultDiffRename(Path(entries[index + 1]), Path(raw_path))
            renamed_paths.append(rename)
            touched_paths.append(rename.new_path)
            index += 2
            continue
        path = Path(raw_path)
        touched_paths.append(path)
        if "A" in status:
            added_paths.append(path)
        if "D" in status:
            deleted_paths.append(path)
        if "M" in status:
            changed_paths.append(path)
        index += 1

    return _ParsedGitStatus(
        touched_paths=tuple(_dedupe_paths(touched_paths)),
        changed_paths=tuple(_dedupe_paths(changed_paths)),
        added_paths=tuple(_dedupe_paths(added_paths)),
        deleted_paths=tuple(_dedupe_paths(deleted_paths)),
        renamed_paths=tuple(renamed_paths),
    )


def _validate_touched_path(
    path: Path,
    *,
    allowed_paths: tuple[Path, ...],
    protected_paths: tuple[Path, ...],
) -> tuple[str, ...]:
    errors: list[str] = []
    if path.is_absolute() or ".." in path.parts:
        errors.append(f"Connection touched an unsafe path: {path.as_posix()}.")
    if any(_is_relative_to(path, protected_path) for protected_path in protected_paths):
        errors.append(f"Connection touched a protected path: {path.as_posix()}.")
    if path.suffix.lower() != ".md":
        errors.append(f"Connection touched a non-Markdown path: {path.as_posix()}.")
    if not any(_is_relative_to(path, allowed_path) for allowed_path in allowed_paths):
        errors.append(f"Connection touched a disallowed path: {path.as_posix()}.")
    return tuple(errors)


async def _can_use_agent_tool(
    vault_path: Path,
    tool_name: str,
    tool_input: dict[str, object],
    context: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    del context
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        if _is_forbidden_shell_command(command):
            return PermissionResultDeny(
                message="Slack Vault connection agents may not run Git or "
                "destructive shell commands."
            )
        return PermissionResultAllow()
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        for path in _tool_file_paths(tool_input):
            errors = _validate_touched_path(
                _relative_tool_path(vault_path, path),
                allowed_paths=DEFAULT_ALLOWED_CONNECTION_PATHS,
                protected_paths=DEFAULT_PROTECTED_CONNECTION_PATHS,
            )
            if errors:
                return PermissionResultDeny(message=" ".join(errors))
    return PermissionResultAllow()


def _is_forbidden_shell_command(command: str) -> bool:
    tokens = {token.strip(";&|()") for token in command.split()}
    if tokens & {
        "git",
        "rm",
        "mv",
        "cp",
        "chmod",
        "chown",
        "python",
        "python3",
        "uv",
        "curl",
        "wget",
    }:
        return True
    return ".git" in command or "90 System/agent-skills" in command


def _tool_file_paths(tool_input: dict[str, object]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(Path(value))
    return tuple(paths)


def _relative_tool_path(vault_path: Path, path: Path) -> Path:
    if not path.is_absolute():
        return path
    try:
        return path.resolve().relative_to(vault_path.resolve())
    except ValueError:
        return path


def _assistant_text(message: AssistantMessage) -> tuple[str, ...]:
    texts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            texts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            texts.append(f"Tool used: {block.name}")
    return tuple(texts)


def _agent_touched_paths(
    before: VaultDiffInspection,
    after: VaultDiffInspection,
) -> tuple[Path, ...]:
    before_paths = {path.as_posix() for path in before.touched_paths}
    return tuple(
        path for path in after.touched_paths if path.as_posix() not in before_paths
    )


def _display_vault_path(vault_path: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(vault_path.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _validate_source_artifacts(
    vault_path: Path,
    *,
    source_id: str,
    primary_note_path: Path | None,
) -> tuple[str, ...]:
    errors: list[str] = []
    source_path = source_record_path(vault_path, source_id)
    if not source_path.is_file():
        errors.append(f"Source record is missing after connection: {source_id}.")
    elif source_id not in source_path.read_text(encoding="utf-8"):
        errors.append(f"Source record no longer references source ID: {source_id}.")

    if primary_note_path is None:
        return tuple(errors)
    note_path = _absolute_vault_path(vault_path, primary_note_path)
    if not note_path.is_file():
        errors.append(
            f"Primary knowledge note is missing: {primary_note_path.as_posix()}."
        )
        return tuple(errors)

    note_text = note_path.read_text(encoding="utf-8")
    if source_id not in note_text:
        errors.append(
            f"Primary knowledge note no longer references source ID: {source_id}."
        )
    if "## Sources" not in note_text:
        errors.append("Primary knowledge note no longer has a Sources section.")
    return tuple(errors)


def _validate_wikilinks(
    vault_path: Path,
    touched_paths: tuple[Path, ...],
) -> tuple[str, ...]:
    known_targets = _known_wikilink_targets(vault_path)
    errors: list[str] = []
    for relative_path in touched_paths:
        absolute_path = _absolute_vault_path(vault_path, relative_path)
        if not absolute_path.is_file() or absolute_path.suffix.lower() != ".md":
            continue
        text = absolute_path.read_text(encoding="utf-8")
        for raw_target in _WIKILINK_PATTERN.findall(text):
            target = _normalize_wikilink_target(raw_target)
            if target and target not in known_targets:
                errors.append(
                    "Connection introduced an unresolved wikilink in "
                    f"{relative_path.as_posix()}: [[{raw_target}]]."
                )
    return tuple(errors)


def _known_wikilink_targets(vault_path: Path) -> set[str]:
    targets: set[str] = set()
    for path in vault_path.rglob("*.md"):
        if ".git" in path.parts:
            continue
        relative_path = path.relative_to(vault_path)
        targets.add(relative_path.as_posix())
        targets.add(relative_path.with_suffix("").as_posix())
        targets.add(path.stem)
        metadata = _frontmatter_metadata(path)
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            targets.add(title.strip())
        aliases = metadata.get("aliases")
        if isinstance(aliases, tuple | list):
            targets.update(
                alias.strip()
                for alias in aliases
                if isinstance(alias, str) and alias.strip()
            )
    return targets


def _frontmatter_metadata(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_FRONTMATTER_BOUNDARY):
        return {}
    end_index = text.find("\n---\n", len(_FRONTMATTER_BOUNDARY))
    if end_index == -1:
        return {}
    metadata: dict[str, object] = {}
    for line in text[len(_FRONTMATTER_BOUNDARY) : end_index].splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        metadata[key.strip()] = _parse_simple_frontmatter_value(raw_value.strip())
    return metadata


def _parse_simple_frontmatter_value(value: str) -> object:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return ()
        return tuple(_strip_quotes(part.strip()) for part in inner.split(","))
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _normalize_wikilink_target(raw_target: str) -> str:
    target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
    if target.endswith(".md"):
        return target[:-3]
    return target


def _changed_line_count(vault_path: Path, *, added_paths: tuple[Path, ...]) -> int:
    total = _numstat_line_count(
        _combined_git_output(
            vault_path,
            (("diff", "--numstat"), ("diff", "--cached", "--numstat")),
        )
    )
    for path in added_paths:
        tracked = _git(
            vault_path,
            "ls-files",
            "--error-unmatch",
            path.as_posix(),
            check=False,
        )
        if tracked.returncode == 0:
            continue
        full_path = _absolute_vault_path(vault_path, path)
        if full_path.is_file():
            total += len(
                full_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).splitlines()
            )
    return total


def _numstat_line_count(stdout: str) -> int:
    total = 0
    for line in stdout.splitlines():
        added, separator, rest = line.partition("\t")
        if not separator:
            continue
        deleted, _, _path = rest.partition("\t")
        if added == "-" or deleted == "-":
            total += 1_000_000
            continue
        total += int(added) + int(deleted)
    return total


def _combined_git_output(
    cwd: Path,
    command_groups: tuple[tuple[str, ...], ...],
) -> str:
    return "\n".join(_git(cwd, *args).stdout for args in command_groups)


def _ensure_git_repository(vault_path: Path) -> Path:
    result = _git(vault_path, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise VaultConnectionError(
            f"Configured vault path is not inside a Git repository: {vault_path}"
        )
    return Path(result.stdout.strip()).resolve()


def _absolute_vault_path(vault_path: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return vault_path / path


def _is_relative_to(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for path in paths:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)
    return unique_paths


def _git(
    cwd: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", "-C", str(cwd), *args),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise VaultConnectionError(result.stderr.strip() or result.stdout.strip())
    return result
