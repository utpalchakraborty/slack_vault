from __future__ import annotations

from pathlib import Path

import pytest

from slack_vault.cli import main


def test_show_config_prints_redacted_settings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["show-config"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "claude-haiku-4-5-20251001" in captured.out
    assert "slack_obsidian" in captured.out


def test_init_vault_uses_configured_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SLACK_VAULT_OBSIDIAN_PATH", str(tmp_path))

    exit_code = main(["init-vault"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Initialized vault" in captured.out
    assert (tmp_path / "90 System/prompt-guidelines.md").is_file()
