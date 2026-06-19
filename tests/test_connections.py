from __future__ import annotations

import subprocess
from pathlib import Path

from slack_vault.connections import (
    VaultDiffValidationConfig,
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
                "This now links to [[related-note|Related Note]].",
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
