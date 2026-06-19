from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    Message,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from slack_vault.connections import (
    ClaudeAgentVaultConnector,
    ConnectionStatus,
    VaultDiffInspection,
    VaultDiffRename,
    VaultDiffValidationConfig,
    _can_use_agent_tool,
    inspect_vault_diff,
    validate_connection_diff,
)


def test_validate_connection_diff_accepts_expected_markdown_changes(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    primary_note = vault_path / "10 Knowledge/imported-note.md"
    topic_index = vault_path / "30 Maps/topic-index.md"
    primary_note.write_text(
        "\n".join(
            [
                "---",
                'title: "Imported Note"',
                'source_ids: ["source-test"]',
                "---",
                "",
                "# Imported Note",
                "",
                "This now links to [[Related Alias]].",
                "",
                "## Sources",
                "",
                "- [[source-test|Example.docx]]",
            ]
        ),
        encoding="utf-8",
    )
    topic_index.write_text(
        "# Topic Index\n\n- [[imported-note|Imported Note]]\n",
        encoding="utf-8",
    )

    inspection = inspect_vault_diff(vault_path)
    result = validate_connection_diff(
        vault_path,
        inspection,
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
        ),
    )

    assert result.ok is True
    assert result.errors == ()
    assert result.stageable_paths == (
        Path("10 Knowledge/imported-note.md"),
        Path("30 Maps/topic-index.md"),
    )


def test_validate_connection_diff_rejects_protected_skill_edits(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    skill_path = (
        vault_path
        / "90 System/agent-skills/slack-vault/skills/connect-imported-document"
        / "SKILL.md"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Skill\n", encoding="utf-8")

    inspection = inspect_vault_diff(vault_path)
    result = validate_connection_diff(
        vault_path,
        inspection,
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
        ),
    )

    assert result.ok is False
    assert any("protected path" in error for error in result.errors)
    assert result.stageable_paths == ()


def test_validate_connection_diff_rejects_obsidian_app_state(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    workspace = vault_path / ".obsidian/workspace.json"
    workspace.parent.mkdir()
    workspace.write_text("{}", encoding="utf-8")

    inspection = inspect_vault_diff(vault_path)
    result = validate_connection_diff(
        vault_path,
        inspection,
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
        ),
    )

    assert result.ok is False
    assert any("protected path" in error for error in result.errors)
    assert any("non-Markdown path" in error for error in result.errors)


def test_validate_connection_diff_rejects_unresolved_wikilinks(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    primary_note = vault_path / "10 Knowledge/imported-note.md"
    primary_note.write_text(
        "\n".join(
            [
                "# Imported Note",
                "",
                "This links to [[missing-note|Missing Note]].",
                "",
                "## Sources",
                "",
                "- source-test",
            ]
        ),
        encoding="utf-8",
    )

    inspection = inspect_vault_diff(vault_path)
    result = validate_connection_diff(
        vault_path,
        inspection,
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
        ),
    )

    assert result.ok is False
    assert any("unresolved wikilink" in error for error in result.errors)


def test_validate_connection_diff_rejects_removed_source_reference(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    primary_note = vault_path / "10 Knowledge/imported-note.md"
    primary_note.write_text(
        "\n".join(
            [
                "# Imported Note",
                "",
                "The source reference was accidentally removed.",
                "",
                "## Sources",
            ]
        ),
        encoding="utf-8",
    )

    inspection = inspect_vault_diff(vault_path)
    result = validate_connection_diff(
        vault_path,
        inspection,
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
        ),
    )

    assert result.ok is False
    assert any("no longer references source ID" in error for error in result.errors)


def test_validate_connection_diff_rejects_destructive_or_large_diffs(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    inspection = VaultDiffInspection(
        touched_paths=(
            Path("10 Knowledge/imported-note.md"),
            Path("30 Maps/topic-index.md"),
            Path("30 Maps/renamed-index.md"),
        ),
        changed_paths=(Path("10 Knowledge/imported-note.md"),),
        added_paths=(),
        deleted_paths=(Path("30 Maps/topic-index.md"),),
        renamed_paths=(
            VaultDiffRename(
                old_path=Path("30 Maps/topic-index.md"),
                new_path=Path("30 Maps/renamed-index.md"),
            ),
        ),
        diff_stat="",
        changed_line_count=10,
    )

    result = validate_connection_diff(
        vault_path,
        inspection,
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
            max_touched_paths=1,
            max_changed_lines=1,
        ),
    )

    assert result.ok is False
    assert result.stageable_paths == ()
    assert any("too many paths" in error for error in result.errors)
    assert any("too many lines" in error for error in result.errors)
    assert any("deleted a path" in error for error in result.errors)
    assert any("renamed a path" in error for error in result.errors)


def test_validate_connection_diff_rejects_empty_agent_diffs(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)

    result = validate_connection_diff(
        vault_path,
        VaultDiffInspection(
            touched_paths=(),
            changed_paths=(),
            added_paths=(),
            deleted_paths=(),
            renamed_paths=(),
            diff_stat="",
            changed_line_count=0,
        ),
        VaultDiffValidationConfig(
            source_id="source-test",
            primary_note_path=Path("10 Knowledge/imported-note.md"),
        ),
    )

    assert result.ok is False
    assert result.errors == ("No vault changes were produced by the connection agent.",)


def test_claude_agent_vault_connector_validates_agent_changes(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    primary_note = vault_path / "10 Knowledge/imported-note.md"
    primary_note.write_text(
        primary_note.read_text(encoding="utf-8").replace(
            "Initial body.",
            "Baseline synthesized body.",
        ),
        encoding="utf-8",
    )
    fake_query = _FakeAgentQuery(vault_path)
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=vault_path
        / "90 System/agent-skills/upstream/obsidian-skills",
        custom_skills_path=vault_path / "90 System/agent-skills/slack-vault",
        max_turns=7,
        anthropic_api_key="test-key",
        query_fn=fake_query,
    )

    result = connector.connect(
        vault_path,
        source_id="source-test",
        source_record_path=vault_path / "20 Sources/sources/source-test.md",
        primary_note_path=primary_note,
    )

    assert result.status is ConnectionStatus.COMPLETED
    assert result.touched_paths == (Path("30 Maps/topic-index.md"),)
    assert result.agent_summary == "Connected imported note."
    assert fake_query.prompt is not None
    assert "Source ID: source-test" in fake_query.prompt
    assert "slack-vault:connect-imported-document" in fake_query.prompt
    assert fake_query.options is not None
    assert fake_query.options.max_turns == 7
    assert fake_query.options.tools == [
        "Read",
        "Edit",
        "MultiEdit",
        "Write",
        "Bash",
        "Skill",
    ]
    assert fake_query.options.env == {"ANTHROPIC_API_KEY": "test-key"}
    assert fake_query.options.mcp_servers == {}
    assert fake_query.options.strict_mcp_config is True
    assert fake_query.options.setting_sources == []
    assert fake_query.options.skills == "all"
    assert fake_query.options.plugins == [
        {
            "type": "local",
            "path": str(vault_path / "90 System/agent-skills/upstream/obsidian-skills"),
        },
        {
            "type": "local",
            "path": str(vault_path / "90 System/agent-skills/slack-vault"),
        },
    ]
    permission = asyncio.run(
        fake_query.options.can_use_tool(
            "Write",
            {"file_path": str(vault_path / "30 Maps/another-index.md")},
            ToolPermissionContext(),
        )
    )
    assert isinstance(permission, PermissionResultAllow)


def test_claude_agent_vault_connector_reports_validation_failure(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    fake_query = _FakeAgentQuery(vault_path, unsafe=True)
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=vault_path / "upstream",
        custom_skills_path=vault_path / "custom",
        query_fn=fake_query,
    )

    result = connector.connect(
        vault_path,
        source_id="source-test",
        source_record_path=vault_path / "20 Sources/sources/source-test.md",
        primary_note_path=vault_path / "10 Knowledge/imported-note.md",
    )

    assert result.status is ConnectionStatus.VALIDATION_FAILED
    assert any("protected path" in error for error in result.validation_errors)


def test_claude_agent_vault_connector_skips_without_primary_note(
    tmp_path: Path,
) -> None:
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=tmp_path / "upstream",
        custom_skills_path=tmp_path / "custom",
        query_fn=_ExplodingAgentQuery(),
    )

    result = connector.connect(
        tmp_path,
        source_id="source-test",
        source_record_path=tmp_path / "20 Sources/sources/source-test.md",
        primary_note_path=None,
    )

    assert result.status is ConnectionStatus.SKIPPED
    assert result.validation_errors == (
        "Connection requires a synthesized primary knowledge note.",
    )


def test_claude_agent_vault_connector_reports_agent_exceptions(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=vault_path / "upstream",
        custom_skills_path=vault_path / "custom",
        query_fn=_ExplodingAgentQuery(),
    )

    result = connector.connect(
        vault_path,
        source_id="source-test",
        source_record_path=vault_path / "20 Sources/sources/source-test.md",
        primary_note_path=vault_path / "10 Knowledge/imported-note.md",
    )

    assert result.status is ConnectionStatus.AGENT_FAILED
    assert result.validation_errors == ("agent exploded",)


def test_claude_agent_vault_connector_reports_sdk_errors(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=vault_path / "upstream",
        custom_skills_path=vault_path / "custom",
        query_fn=_ErrorAgentQuery(),
    )

    result = connector.connect(
        vault_path,
        source_id="source-test",
        source_record_path=vault_path / "20 Sources/sources/source-test.md",
        primary_note_path=vault_path / "10 Knowledge/imported-note.md",
    )

    assert result.status is ConnectionStatus.AGENT_FAILED
    assert result.agent_summary == "Could not connect."
    assert result.validation_errors == (
        "model refused",
        "API error status: 500",
    )


def test_claude_agent_vault_connector_preserves_errors_before_sdk_exception(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=vault_path / "upstream",
        custom_skills_path=vault_path / "custom",
        query_fn=_ErrorThenRaisesAgentQuery(),
    )

    result = connector.connect(
        vault_path,
        source_id="source-test",
        source_record_path=vault_path / "20 Sources/sources/source-test.md",
        primary_note_path=vault_path / "10 Knowledge/imported-note.md",
    )

    assert result.status is ConnectionStatus.AGENT_FAILED
    assert result.agent_summary == "Use an Anthropic API key instead."
    assert result.validation_errors == ("API error status: 403",)


def test_claude_agent_vault_connector_uses_assistant_text_summary(
    tmp_path: Path,
) -> None:
    vault_path = _connected_fixture_vault(tmp_path)
    connector = ClaudeAgentVaultConnector(
        obsidian_skills_path=vault_path / "upstream",
        custom_skills_path=vault_path / "custom",
        query_fn=_AssistantOnlyAgentQuery(),
    )

    result = connector.connect(
        vault_path,
        source_id="source-test",
        source_record_path=vault_path / "20 Sources/sources/source-test.md",
        primary_note_path=vault_path / "10 Knowledge/imported-note.md",
    )

    assert result.status is ConnectionStatus.VALIDATION_FAILED
    assert result.agent_summary == "I inspected likely links.\nTool used: Edit"


def test_connection_agent_permissions_deny_unsafe_tools(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    denied_git = asyncio.run(
        _can_use_agent_tool(
            vault_path,
            "Bash",
            {"command": "git status"},
            ToolPermissionContext(),
        )
    )
    allowed_obsidian = asyncio.run(
        _can_use_agent_tool(
            vault_path,
            "Bash",
            {"command": "obsidian search query=connections"},
            ToolPermissionContext(),
        )
    )
    denied_protected = asyncio.run(
        _can_use_agent_tool(
            vault_path,
            "Write",
            {"file_path": str(vault_path / ".obsidian/workspace.json")},
            ToolPermissionContext(),
        )
    )
    denied_outside = asyncio.run(
        _can_use_agent_tool(
            vault_path,
            "Edit",
            {"path": str(tmp_path / "outside.md")},
            ToolPermissionContext(),
        )
    )
    allowed_markdown = asyncio.run(
        _can_use_agent_tool(
            vault_path,
            "MultiEdit",
            {"path": str(vault_path / "10 Knowledge/follow-up.md")},
            ToolPermissionContext(),
        )
    )

    assert isinstance(denied_git, PermissionResultDeny)
    assert isinstance(allowed_obsidian, PermissionResultAllow)
    assert isinstance(denied_protected, PermissionResultDeny)
    assert "protected path" in denied_protected.message
    assert isinstance(denied_outside, PermissionResultDeny)
    assert "unsafe path" in denied_outside.message
    assert isinstance(allowed_markdown, PermissionResultAllow)


class _FakeAgentQuery:
    def __init__(self, vault_path: Path, *, unsafe: bool = False) -> None:
        self.vault_path = vault_path
        self.unsafe = unsafe
        self.prompt: str | None = None
        self.options: Any | None = None

    async def __call__(
        self,
        *,
        prompt: object,
        options: object,
    ) -> AsyncIterator[Message]:
        self.prompt = await _prompt_text(prompt)
        self.options = options
        if self.unsafe:
            target = self.vault_path / ".obsidian/workspace.json"
            target.parent.mkdir(exist_ok=True)
            target.write_text("{}", encoding="utf-8")
        else:
            target = self.vault_path / "30 Maps/topic-index.md"
            target.write_text(
                "# Topic Index\n\n- [[imported-note|Imported Note]]\n",
                encoding="utf-8",
            )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session-test",
            result="Connected imported note.",
        )


class _ExplodingAgentQuery:
    def __init__(self) -> None:
        self.should_yield = False

    def __call__(self, *, prompt: object, options: object) -> AsyncIterator[Message]:
        del prompt, options

        async def _iterator() -> AsyncIterator[Message]:
            if self.should_yield:
                yield _success_message("unreachable")
            raise RuntimeError("agent exploded")

        return _iterator()


class _ErrorAgentQuery:
    async def __call__(
        self,
        *,
        prompt: object,
        options: object,
    ) -> AsyncIterator[Message]:
        del prompt, options
        yield ResultMessage(
            subtype="error_max_turns",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="session-test",
            result="Could not connect.",
            errors=["model refused"],
            api_error_status=500,
        )


class _ErrorThenRaisesAgentQuery:
    async def __call__(
        self,
        *,
        prompt: object,
        options: object,
    ) -> AsyncIterator[Message]:
        del prompt, options
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="session-test",
            result="Use an Anthropic API key instead.",
            api_error_status=403,
        )
        raise RuntimeError("Claude Code returned an error result: success")


class _AssistantOnlyAgentQuery:
    async def __call__(
        self,
        *,
        prompt: object,
        options: object,
    ) -> AsyncIterator[Message]:
        del prompt, options
        yield AssistantMessage(
            content=[
                TextBlock(text="I inspected likely links."),
                ToolUseBlock(id="tool-1", name="Edit", input={}),
            ],
            model="claude-test",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session-test",
            result=None,
        )


def _success_message(result: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="session-test",
        result=result,
    )


async def _prompt_text(prompt: object) -> str:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, AsyncIterable):
        chunks: list[str] = []
        async for message in cast(AsyncIterable[dict[str, Any]], prompt):
            payload = message.get("message")
            if not isinstance(payload, dict):
                continue
            content = payload.get("content")
            if isinstance(content, str):
                chunks.append(content)
        return "\n".join(chunks)
    raise AssertionError(f"Unexpected prompt type: {type(prompt)}")


def _connected_fixture_vault(tmp_path: Path) -> Path:
    vault_path = tmp_path / "vault"
    _init_git_repo(vault_path)
    (vault_path / "10 Knowledge").mkdir(parents=True)
    (vault_path / "20 Sources/sources").mkdir(parents=True)
    (vault_path / "30 Maps").mkdir(parents=True)
    (vault_path / "10 Knowledge/imported-note.md").write_text(
        "\n".join(
            [
                "---",
                'title: "Imported Note"',
                'source_ids: ["source-test"]',
                "---",
                "",
                "# Imported Note",
                "",
                "Initial body.",
                "",
                "## Sources",
                "",
                "- [[source-test|Example.docx]]",
            ]
        ),
        encoding="utf-8",
    )
    (vault_path / "10 Knowledge/related-note.md").write_text(
        "\n".join(
            [
                "---",
                'title: "Related Note"',
                'aliases: ["Related Alias"]',
                "---",
                "",
                "# Related Note",
            ]
        ),
        encoding="utf-8",
    )
    (vault_path / "20 Sources/sources/source-test.md").write_text(
        "---\nsource_id: source-test\n---\n\n# Example.docx\n",
        encoding="utf-8",
    )
    (vault_path / "30 Maps/topic-index.md").write_text(
        "# Topic Index\n",
        encoding="utf-8",
    )
    _run_git(vault_path, "add", ".")
    _run_git(vault_path, "commit", "-m", "Initialize fixture vault")
    return vault_path


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _run_git(path, "init")
    _run_git(path, "config", "user.name", "Slack Vault Test")
    _run_git(path, "config", "user.email", "slack-vault@example.test")


def _run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
    )
