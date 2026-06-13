"""Command line interface for Slack Vault."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from slack_vault.ai import AITextProvider, AnthropicAIProvider, RetryingAITextProvider
from slack_vault.config import Settings
from slack_vault.dev_cleanup import clean_poc_data
from slack_vault.enhancement import AnthropicEvidenceEnhancer, EvidenceEnhancer
from slack_vault.git_vault import GitVaultCommitter
from slack_vault.ingest import IngestProcessingError, ingest_local_file
from slack_vault.log_setup import configure_logging
from slack_vault.synthesis import AnthropicKnowledgeSynthesizer, KnowledgeSynthesizer
from slack_vault.vault_bootstrap import bootstrap_vault

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(prog="slack-vault")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved settings.")

    cleanup_parser = subparsers.add_parser(
        "clean-poc-data",
        help="Remove generated local POC vault files and local archive data.",
    )
    cleanup_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of generated POC files and local archive data.",
    )

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
    ingest_parser.add_argument(
        "--enhance",
        action="store_true",
        help="Run optional AI evidence enhancement before writing the source record.",
    )
    ingest_parser.add_argument(
        "--synthesize",
        action="store_true",
        help="Run AI classification and knowledge-note synthesis after extraction.",
    )
    ingest_parser.add_argument(
        "--no-git-commit",
        action="store_true",
        help="Write vault files without committing them to the vault Git repository.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Slack Vault CLI."""

    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    log_path = configure_logging(settings)
    logger.info("CLI command started command=%s", args.command)

    if args.command == "show-config":
        print(settings.as_json())
        return 0

    if args.command == "clean-poc-data":
        if not args.yes:
            raise ValueError("clean-poc-data requires --yes")
        cleanup_result = clean_poc_data(settings)
        print(f"Vault: {cleanup_result.vault_path}")
        print(f"Archive: {cleanup_result.archive_path}")
        print(f"Removed vault files: {len(cleanup_result.removed_vault_paths)}")
        for path in cleanup_result.removed_vault_paths:
            print(f"- {path}")
        print(f"Removed archive: {cleanup_result.removed_archive}")
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
        print(f"Log file: {log_path}")
        evidence_enhancer: EvidenceEnhancer | None = None
        knowledge_synthesizer: KnowledgeSynthesizer | None = None
        vault_committer = None if args.no_git_commit else GitVaultCommitter()
        if vault_committer is not None:
            vault_committer.ensure_clean_worktree(settings.obsidian_vault_path)
        ai_provider: AITextProvider | None = None
        if args.enhance or args.synthesize:
            ai_provider = RetryingAITextProvider(
                AnthropicAIProvider.from_settings(settings),
                retry=settings.ai.retry,
            )
        if args.enhance:
            if ai_provider is None:
                raise ValueError("AI provider is required for evidence enhancement")
            evidence_enhancer = AnthropicEvidenceEnhancer(ai_provider)
        if args.synthesize:
            if ai_provider is None:
                raise ValueError("AI provider is required for knowledge synthesis")
            knowledge_synthesizer = AnthropicKnowledgeSynthesizer(ai_provider)
        try:
            ingest_result = ingest_local_file(
                Path(args.path),
                settings,
                uploaded_by=args.uploaded_by,
                overwrite_source_record=args.overwrite_source_record,
                evidence_enhancer=evidence_enhancer,
                knowledge_synthesizer=knowledge_synthesizer,
                vault_committer=vault_committer,
            )
        except IngestProcessingError as exc:
            logger.error(
                "CLI ingest failed source_id=%s stage=%s reason=%s",
                exc.source_id,
                exc.stage,
                exc.reason,
            )
            print(f"Source: {exc.source_id}")
            print(f"Ingest failed during {exc.stage}: {exc.reason}")
            print("Vault Git commit: not_created")
            return 1
        print(f"Source: {ingest_result.source_record.source_id}")
        print(f"Archive URI: {ingest_result.archived_source.uri}")
        print(f"Evidence artifact: {ingest_result.evidence_artifact.path}")
        print(f"Extraction status: {ingest_result.extraction_result.status.value}")
        print(f"Extractor: {ingest_result.extraction_result.extractor_name}")
        print(f"Evidence blocks: {len(ingest_result.extraction_result.evidence)}")
        enhancement_status = (
            "not_requested"
            if ingest_result.enhancement_result is None
            else ingest_result.enhancement_result.status.value
        )
        print(f"Enhancement status: {enhancement_status}")
        if ingest_result.enhancement_result is not None:
            print(
                "Enhanced evidence blocks: "
                f"{len(ingest_result.enhancement_result.enhanced_evidence)}"
            )
        synthesis_status = (
            "not_requested"
            if ingest_result.synthesis_result is None
            else ingest_result.synthesis_result.status.value
        )
        print(f"Synthesis status: {synthesis_status}")
        if ingest_result.synthesis_result is not None:
            synthesis_result = ingest_result.synthesis_result
            if synthesis_result.note is not None:
                print(f"Knowledge note: {synthesis_result.note.path}")
                print(f"Created knowledge note: {synthesis_result.note.created}")
            if synthesis_result.error_message is not None:
                print(f"Synthesis note: {synthesis_result.error_message}")
        if ingest_result.git_commit is None:
            print("Vault Git commit: not_requested")
        elif ingest_result.git_commit.committed:
            print(f"Vault Git commit: {ingest_result.git_commit.commit_hash}")
        else:
            print(
                f"Vault Git commit: skipped ({ingest_result.git_commit.skipped_reason})"
            )
        print(f"Source record: {ingest_result.source_record.path}")
        print(f"Created source record: {ingest_result.source_record.created}")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")
