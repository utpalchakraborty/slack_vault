# Implementation Plan

This document defines a phased implementation plan for the Slack Vault proof of
concept. It is intentionally ordered so each phase creates a working slice that
can be tested before the next layer is added.

## 1. POC Definition

The proof of concept is successful when a user can:

1. Post a supported document into a Slack ingestion channel.
2. Have the system archive the original document outside Git.
3. Have the system extract useful evidence from the document.
4. Have AI create or update Markdown notes in an Obsidian vault.
5. Have the vault changes committed to Git.
6. Clone or open the vault directly in Obsidian and browse the generated notes.
7. Ask the Slack bot a question and receive an answer with citations to vault
   notes and source records.

## 2. Implementation Principles

- Keep Markdown in the Obsidian vault as the canonical knowledge artifact.
- Keep large original source files outside Git.
- Make backend indexes derived from the vault, not canonical.
- Build local-first, then add shared deployment pieces.
- Prefer explicit interfaces around archive storage, AI providers, extraction,
  retrieval, and Slack.
- Keep the first version narrow but end-to-end.
- Make each phase independently testable.

## 3. Proposed Initial Stack

These choices are defaults for the POC and can be revised before coding.

- Language: Python 3.13.
- Project tooling: `uv`, `ruff`, `mypy`, `pytest`, `pytest-cov`, and
  `pre-commit`.
- Slack integration: Slack Bolt for Python.
- Operational state: SQLite for local POC.
- Vault format: plain Markdown with YAML frontmatter and Obsidian wikilinks.
- Archive providers:
  - local filesystem for POC;
  - Google Cloud Storage after local flow works.
- AI providers:
  - Anthropic first, behind an internal provider interface.
  - Default model: `claude-haiku-4-5-20251001`.
- Initial document processing:
  - Phase 2a: deterministic extraction into source-grounded evidence;
  - Phase 2b: optional AI evidence enhancement for noisy or ambiguous sources;
  - Markdown, plain text, PDF, Word, and Excel first;
  - images after the basic loop is working.
- Retrieval:
  - lexical Markdown/frontmatter search first;
  - vector retrieval after the Q&A path is functional.

## 3.1 Current Local Environment Decisions

- Code repository: `/Users/utpalrohan/code/slack_vault`.
- Obsidian vault repository: `/Users/utpalrohan/code/slack_obsidian`.
- The configured vault path is read from `SLACK_VAULT_OBSIDIAN_PATH`.
- The local archive path is read from `SLACK_VAULT_ARCHIVE_PATH` and defaults
  to `.data/archive` in the app repository.
- The AI provider is read from `SLACK_VAULT_AI_PROVIDER` and defaults to
  `anthropic`.
- Anthropic credentials are read from `ANTHROPIC_API_KEY`.
- Haiku 4.5 is configured through `SLACK_VAULT_ANTHROPIC_MODEL`.

## 3.2 Current Progress

Status as of 2026-06-13:

- Phase 0 is complete and pushed.
- Phase 1 is complete and pushed.
- Phase 2a deterministic extraction is implemented in the working tree and ready
  for review.
- Latest code repository commit:
  `c69c53d Implement local archive source registry`.
- Obsidian vault repository commit:
  `8c73576 Initialize Obsidian vault skeleton`.
- Both commits have been pushed to `origin/main`.
- The code repository is configured with `uv`, `ruff`, `mypy`, `pytest`,
  `pytest-cov`, `pre-commit`, `AGENTS.md`, and split Make targets under
  `makefiles/`.
- `make check` passes with no external services configured.
- The Obsidian vault repository opens as a vault and keeps local `.obsidian/`
  app state ignored.

Next implementation phase after Phase 2a review: Phase 2b optional AI evidence
enhancement, or Phase 3 classification and synthesis if no enhancement cases
need to block the local loop.

## 4. Phase 0: Project And Vault Skeleton

### Goal

Create the repository structure, local configuration, and starter Obsidian vault
layout without implementing ingestion logic yet.

### Deliverables

- Python project scaffold.
- `README.md` with local development instructions.
- `.env.example` listing required Slack and AI credentials.
- Local config model for:
  - vault path;
  - archive provider;
  - archive path;
  - Slack channel IDs;
  - AI provider selection.
- Starter vault structure:
  - `vault/00 Inbox/`
  - `vault/10 Knowledge/`
  - `vault/20 Sources/`
  - `vault/30 Maps/`
  - `vault/40 Views/`
  - `vault/90 System/`
- Initial system guidance files:
  - `vault/90 System/ingestion-guidelines.md`
  - `vault/90 System/taxonomy-guidelines.md`
  - `vault/90 System/prompt-guidelines.md`

### Acceptance Criteria

- A user can open `vault/` in Obsidian.
- The vault has a clear starter structure.
- The project can run tests with no external services configured.

### Completion Notes

Phase 0 is implemented across two repositories:

- `/Users/utpalrohan/code/slack_vault` contains the Python application scaffold,
  settings model, CLI, vault bootstrapper, tests, pre-commit configuration,
  Make targets, and contributor/agent guidance.
- `/Users/utpalrohan/code/slack_obsidian` contains the starter Obsidian vault
  directories, source index, map indexes, system guidance files, and Git
  placeholders for empty starter folders.

Validation completed before moving to Phase 1:

- `make check` in the code repository.
- Manual open of `/Users/utpalrohan/code/slack_obsidian` in Obsidian.
- Git push of both repositories to `origin/main`.

## 5. Phase 1: Archive And Source Registry Core

### Goal

Implement the core source artifact model and local archive provider. This phase
does not require Slack or AI.

### Deliverables

- `ArchiveProvider` interface.
- `LocalFilesystemArchiveProvider`.
- Source artifact metadata model.
- Content hash generation.
- Source ID generation.
- Source record Markdown writer.
- Local CLI command to ingest a file from disk into the archive and source
  registry.

### Acceptance Criteria

- Running a local CLI command against a sample file stores the original file in
  the local archive.
- The command creates a source record under `vault/20 Sources/sources/`.
- The source record includes archive URI, content hash, filename, MIME type,
  created timestamp, and local ingestion metadata.
- Re-ingesting the same file is idempotent or clearly marked as a duplicate.
- Unit tests cover source ID generation, content hashing, archive writes, and
  source record rendering.

### Implementation Notes

Phase 1 is implemented without Slack or AI dependencies.

- `src/slack_vault/archive.py` defines the archive interface, source ingest
  metadata, archived source reference model, SHA-256 hashing, MIME detection,
  and local filesystem archive provider.
- `src/slack_vault/source_registry.py` generates source IDs, renders source
  record Markdown, and writes source records under
  `20 Sources/sources/`.
- `src/slack_vault/ingest.py` coordinates local file ingestion from archive
  write through source record creation.
- `slack-vault ingest-file` and `make ingest-file FILE=...` provide the local
  CLI path.
- Local archive idempotency is content-hash based, so re-ingesting the same
  file reuses the existing archived source metadata.

Validation:

- `make check` passes with 17 tests and 94.09% coverage.
- `make -n ingest-file FILE=README.md` confirms the Make wrapper command.

## 6. Phase 2: Document Extraction And Evidence Enhancement

Phase 2 is split into a required deterministic extraction phase and an optional
AI enhancement phase. The split keeps the ingestion path auditable and testable
while still allowing AI to improve difficult documents.

### 6.1 Phase 2a: Deterministic Document Extraction

#### Goal

Convert source documents into normalized extracted evidence that can later be
used by AI enhancement, AI synthesis, and retrieval.

Phase 2a must not require Slack or AI credentials.

#### Deliverables

- `DocumentExtractor` interface.
- Initial deterministic extractors for:
  - Markdown;
  - plain text;
  - PDF;
  - Word documents;
  - Excel workbooks.
- Normalized extraction result model.
- Evidence location model:
  - file-level for text/Markdown;
  - page-level for PDF;
  - heading, paragraph, or table-level for Word where practical;
  - sheet and cell-range-level for Excel when added.
- Extraction status updates in source records.
- CLI path that archives a local file and writes extracted evidence into the
  source record.
- Tests using small committed fixtures or programmatically generated fixtures.

#### Acceptance Criteria

- Sample Markdown, text, PDF, Word, and Excel files produce readable extracted
  evidence.
- Source records show extraction status and extracted evidence.
- Extraction failures are recorded without corrupting the vault.
- Tests cover all initial extractors using small fixtures.
- Tests verify source anchors such as file names, headings, PDF page numbers, and
  Word paragraph or table references.

#### Implementation Notes

Phase 2a is implemented in the working tree.

- `src/slack_vault/extraction.py` defines `DocumentExtractor`,
  `ExtractionResult`, `EvidenceBlock`, and deterministic extractors for
  Markdown, plain text, PDF, DOCX, and XLSX.
- PDF extraction uses `pypdf`; DOCX and XLSX extraction use the ZIP/XML
  structure directly.
- `slack-vault ingest-file` archives the source, extracts evidence from the
  archived copy, writes extraction status and evidence into the source record,
  and prints extraction status in CLI output.
- Extraction failures and unsupported file types are recorded as source-record
  status instead of aborting source-record creation.

Validation:

- `make check` passes with 27 tests and 91.35% coverage.

#### Test Fixture Strategy

- Use small synthetic fixtures checked into `tests/fixtures/` for stable
  extractor behavior.
- Prefer programmatically generated PDF and Word fixtures when the generation
  code is simple enough to keep the expected structure obvious.
- Use sanitized real-world examples only when synthetic fixtures miss important
  layout cases. Do not commit confidential source documents or original
  uploaded source files.
- Keep any large or sensitive real-world examples outside Git and archive them
  through the configured archive provider when needed for manual testing.

### 6.2 Phase 2b: Optional AI Evidence Enhancement

#### Goal

Use AI to clean up, structure, or enrich extracted evidence when deterministic
extraction is incomplete, noisy, or ambiguous.

Phase 2b is optional per document and per evidence block. A clean Markdown file
may skip it entirely, while a scanned PDF, broken PDF table, or complex workbook
may need it.

#### Deliverables

- `EvidenceEnhancer` interface that accepts deterministic evidence and returns
  enhanced evidence while preserving source anchors.
- Enhancement status updates in source records, separate from extraction status.
- AI-assisted preprocessing prompts for:
  - cleaning noisy PDF text;
  - inferring sections from weak layout;
  - interpreting tables while preserving cited source locations;
  - summarizing large evidence blocks without replacing the original evidence.
- Mocked tests for prompt input/output parsing and failure handling.

#### Acceptance Criteria

- Phase 2a output remains usable when Phase 2b is disabled or unavailable.
- AI enhancement never replaces the archived original source or the deterministic
  extracted evidence.
- Enhanced evidence preserves links back to deterministic source locations.
- Enhancement failures are recorded without blocking deterministic extraction.
- Classification, taxonomy selection, note matching, and knowledge-note writing
  remain Phase 3 responsibilities.

## 7. Phase 3: AI Classification And Knowledge Synthesis

### Goal

Use AI to transform extracted or enhanced evidence into Obsidian knowledge notes.

### Deliverables

- `AIProvider` interface.
- Initial OpenAI or Anthropic provider implementation.
- Prompt templates for:
  - source classification;
  - taxonomy suggestion;
  - existing note matching;
  - knowledge note creation;
  - knowledge note update;
  - citation generation.
- Vault reader for existing notes.
- Vault writer for knowledge notes.
- Initial note update strategy:
  - create new note when no strong match exists;
  - update an existing note when a strong match exists;
  - record uncertainty in note metadata when confidence is low.
- CLI command for local end-to-end ingest:
  - archive source;
  - extract evidence;
  - optionally enhance evidence;
  - classify;
  - create or update knowledge notes.

### Acceptance Criteria

- A sample source document creates a source record and at least one knowledge
  note.
- A second related source document updates the same knowledge note when
  appropriate.
- Generated knowledge notes include frontmatter, backlinks, source citations,
  and a readable Markdown body.
- The generated vault remains usable in Obsidian.
- Tests cover prompt input/output parsing with mocked AI responses.

## 8. Phase 4: Git Commit Integration

### Goal

Make every successful ingestion auditable through Git history.

### Deliverables

- Git repository detection and validation.
- Git commit helper.
- Structured ingestion commit messages.
- Local development mode for running without committing.
- Clear error handling when the vault has uncommitted conflicting changes.

### Acceptance Criteria

- A successful local ingest creates one Git commit.
- The commit includes source records, knowledge notes, maps/views if changed,
  and no archive binaries.
- The commit message includes source ID and updated notes.
- Tests cover commit message generation and dirty-repo handling where practical.

## 9. Phase 5: Local Retrieval And Q&A

### Goal

Implement question answering over the vault before adding Slack Q&A.

### Deliverables

- Vault search interface.
- Frontmatter metadata search.
- Simple lexical retrieval over Markdown content.
- Citation-aware context builder.
- AI answer generation prompt.
- Local CLI command:
  - ask a question;
  - retrieve relevant notes and source records;
  - generate an answer with citations.

### Acceptance Criteria

- A local question about ingested content returns an answer grounded in vault
  notes.
- The answer includes citations to knowledge notes and source records.
- If the vault lacks evidence, the answer says so.
- Tests cover retrieval ranking basics and answer prompt assembly with mocked AI
  responses.

## 10. Phase 6: Slack Ingestion POC

### Goal

Connect the working local ingestion pipeline to Slack.

### Deliverables

- Slack app configuration documentation.
- Slack event handler for the ingestion channel.
- File download from Slack.
- Slack metadata capture:
  - workspace ID;
  - channel ID;
  - message timestamp;
  - file ID;
  - uploader;
  - optional message comment.
- Ingestion job creation.
- Basic SQLite operational state for jobs and Slack events.
- Slack response when ingestion succeeds or fails.

### Acceptance Criteria

- Uploading a supported file to the configured Slack ingestion channel triggers
  ingestion.
- The original file is stored in the local archive.
- A source record and knowledge note are created or updated.
- A Git commit is created.
- The bot replies in Slack with ingestion status and note references.
- Duplicate Slack events do not create duplicate source records.

## 11. Phase 7: Slack Q&A POC

### Goal

Expose vault question answering through Slack.

### Deliverables

- Slack DM question handling.
- Slack mention/thread question handling.
- Response visibility behavior:
  - DM questions stay in DM;
  - public mentions or thread questions receive public/thread replies.
- Q&A worker using the Phase 5 retrieval and answer generation path.
- Citation formatting suitable for Slack.

### Acceptance Criteria

- A user can ask the bot a question in DM and receive a private answer.
- A user can mention the bot in a channel/thread and receive an answer there.
- Answers include citations to vault notes and source records.
- Unsupported questions produce a clear "not enough evidence" response.

## 12. Phase 8: POC Hardening

### Goal

Make the POC reliable enough for repeated use by a small group.

### Deliverables

- Better job status tracking.
- Retry behavior for transient Slack, archive, and AI failures.
- Dead-letter state for failed jobs.
- Basic structured logging.
- Basic rate limit handling.
- Idempotency tests for Slack events and duplicate files.
- Regression fixtures for representative documents.
- Developer runbook for local operation.

### Acceptance Criteria

- Failed ingestions are visible and debuggable.
- Retrying a failed job does not duplicate source records or notes.
- The system can process a small batch of sample documents repeatedly.
- A developer can start the local bot from documented commands.

## 13. Phase 9: Shared Deployment Preparation

### Goal

Prepare the POC architecture for a shared environment without overbuilding
production too early.

### Deliverables

- `GoogleCloudArchiveProvider` backed by Google Cloud Storage.
- Configuration profiles for local and shared deployments.
- Deployment target recommendation.
- Secrets management plan.
- Git-hosted vault repo configuration.
- Operational database recommendation for shared deployment.
- Index rebuild command.
- Backup and recovery notes.

### Acceptance Criteria

- The archive provider can be switched from local filesystem to Google Cloud
  Storage by configuration.
- The vault remains cloneable and usable in Obsidian.
- The shared deployment plan identifies required services, credentials, and
  operational ownership.

## 14. Deferred Until After POC

- Permission-aware retrieval.
- Multiple Slack workspaces or Enterprise Grid.
- Obsidian Sync automation.
- Human approval workflows.
- Advanced spreadsheet extraction.
- Advanced image/OCR extraction.
- Advanced conflict resolution UI.
- Organization-wide taxonomy governance.
- Large-scale vector indexing and re-ranking.
- Continuous ingestion from arbitrary Slack channels.

## 15. Resolved And Remaining Early Decisions

These decisions guide the early implementation phases. Resolved items should
remain documented so future implementation work does not reopen settled Phase 0
choices without a specific reason.

- Confirm Python as the POC implementation language. Decision: Python 3.13.
- Choose first AI provider for implementation: OpenAI or Anthropic. Decision:
  Anthropic.
- Decide whether to initialize this repository as the Git-backed vault repo or
  keep code and vault in separate repositories later. Decision: separate
  repositories; the Obsidian vault repo is
  `/Users/utpalrohan/code/slack_obsidian`.
- Decide whether the first Slack POC uses Socket Mode or a public HTTP endpoint.
  Status: unresolved; not needed for Phase 1.
- Decide whether vector retrieval is required in the POC or can follow the first
  lexical Q&A loop. Status: unresolved; not needed for Phase 1.
