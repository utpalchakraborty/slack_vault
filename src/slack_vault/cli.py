"""Command line interface for Slack Vault."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from slack_vault.config import Settings
from slack_vault.vault_bootstrap import bootstrap_vault


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(prog="slack-vault")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved settings.")

    init_parser = subparsers.add_parser(
        "init-vault",
        help="Create starter directories and guidance in the configured vault.",
    )
    init_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rewrite starter files even when they already exist.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Slack Vault CLI."""

    args = build_parser().parse_args(argv)
    settings = Settings.from_env()

    if args.command == "show-config":
        print(settings.as_json())
        return 0

    if args.command == "init-vault":
        result = bootstrap_vault(
            settings.obsidian_vault_path,
            overwrite=args.overwrite,
        )
        print(f"Initialized vault: {result.vault_path}")
        print(f"Created or verified {len(result.created_paths)} paths")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")
