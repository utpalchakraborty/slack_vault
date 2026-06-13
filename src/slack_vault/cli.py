"""Command line interface for Slack Vault."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from slack_vault.config import Settings
from slack_vault.ingest import ingest_local_file
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

    ingest_parser = subparsers.add_parser(
        "ingest-file",
        help="Archive a local file and write a source record.",
    )
    ingest_parser.add_argument("path", help="Path to the local source file.")
    ingest_parser.add_argument(
        "--uploaded-by",
        help="Optional local uploader/user label for the source record.",
    )
    ingest_parser.add_argument(
        "--overwrite-source-record",
        action="store_true",
        help="Rewrite the source record if it already exists.",
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
        bootstrap_result = bootstrap_vault(
            settings.obsidian_vault_path,
            overwrite=args.overwrite,
        )
        print(f"Initialized vault: {bootstrap_result.vault_path}")
        print(f"Created or verified {len(bootstrap_result.created_paths)} paths")
        return 0

    if args.command == "ingest-file":
        ingest_result = ingest_local_file(
            Path(args.path),
            settings,
            uploaded_by=args.uploaded_by,
            overwrite_source_record=args.overwrite_source_record,
        )
        print(f"Source: {ingest_result.source_record.source_id}")
        print(f"Archive URI: {ingest_result.archived_source.uri}")
        print(f"Source record: {ingest_result.source_record.path}")
        print(f"Created source record: {ingest_result.source_record.created}")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")
