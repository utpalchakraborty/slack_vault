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
  - Obsidian CLI search over vault Markdown first;
  - vector retrieval after the Q&A path is functional.

## 3.1 Current Local Environment Decisions

- Code repository: `/Users/utpalrohan/code/slack_vault`.
- Obsidian vault repository: `/Users/utpalrohan/code/slack_obsidian`.
- The configured vault path is read from `SLACK_VAULT_OBSIDIAN_PATH`.
- The local archive path is read from `SLACK_VAULT_ARCHIVE_PATH` and defaults
  to `.data/archive` in the app repository.
- Original source files and full extracted/enhanced evidence artifacts are kept
  outside the Git-backed Obsidian vault. Vault source records are lightweight
  provenance records with status, counts, and artifact pointers.
- The local log path is read from `SLACK_VAULT_LOG_PATH` and defaults to
  `.data/logs/slack-vault.log` in the app repository.
- Logs rotate daily at midnight, rotated logs are gzip-compressed, and the
  retained rotated-log count is read from `SLACK_VAULT_LOG_BACKUP_COUNT`.
- The AI provider is read from `SLACK_VAULT_AI_PROVIDER` and defaults to
  `anthropic`.
- Anthropic credentials are read from `ANTHROPIC_API_KEY`.
- Haiku 4.5 is configured through `SLACK_VAULT_ANTHROPIC_MODEL`.
- CLI/default settings load `.env` from the current working directory when it
  exists. Real environment variables override matching `.env` values.

## 3.2 Current Progress

Status as of 2026-06-13:

- Phase 0 is complete and pushed.
- Phase 1 is complete and pushed.
- Phase 2a deterministic extraction is complete and pushed.
- Phase 2b provider groundwork is complete and pushed.
- Phase 2b prompt caching is implemented in the Anthropic harness:
  provider/request-level cache configuration, top-level automatic
  `cache_control`, explicit system/uploaded-file cache breakpoints, and cache
  read/write usage fields on `AITextResponse`.
- Phase 2b optional AI evidence enhancement is implemented as an opt-in local
  ingest path:
  - `EvidenceEnhancer`, `EnhancementResult`, and `EnhancedEvidenceBlock`
    models;
  - `AnthropicEvidenceEnhancer` using the text provider harness;
  - source-record `enhancement_status` metadata separate from
    `extraction_status`;
  - full enhanced evidence is stored in the outside-vault evidence artifact and
    preserves links back to deterministic evidence sequence and source location;
  - `slack-vault ingest-file --enhance` and
    `make ingest-file FILE=... ENHANCE=1`.
- Phase 3 initial local synthesis is implemented as an opt-in local ingest
  path:
  - `AnthropicKnowledgeSynthesizer` classifies source evidence, suggests
    taxonomy metadata, asks the model to match existing notes, and writes a
    knowledge note under `10 Knowledge/`;
  - existing notes are scanned from the vault and included in the synthesis
    prompt;
  - strong AI note matches update the existing note, while weak or missing
    matches create a new note and record uncertainty metadata;
  - generated notes include frontmatter, source IDs, Obsidian source backlinks,
    citation lines, summary, and Markdown details;
  - `slack-vault ingest-file --synthesize` and
    `make ingest-file FILE=... SYNTHESIZE=1`.
- Source records no longer embed full extracted/enhanced evidence in the vault.
  `src/slack_vault/evidence_store.py` writes full evidence JSON under
  `.data/archive/derived/evidence/<source-id>/evidence.json`, while vault source
  records retain provenance, status, counts, and artifact pointers.
- Live DOCX smoke testing showed that long-running ingestion needs visible
  progress logs. The app now configures package logging for CLI runs, writes to
  `.data/logs/slack-vault.log` by default, rotates at midnight, gzip-compresses
  rotated logs, and logs archive, extraction, enhancement, source-record, and
  synthesis lifecycle events.
- Live Git-commit smoke testing showed that Anthropic 429 rate limits must not
  produce source-record-only commits when synthesis was requested. AI text calls
  now retry transient provider failures, requested enhancement or synthesis
  failures stop the ingest before vault source-record writes and Git commits,
  and sequential automatic ingest flows have a configurable
  `SLACK_VAULT_AUTOMATIC_INGEST_DELAY_SECONDS` pause between documents for the
  later Slack ingestion worker.
- Phase 4 Git commit integration is implemented for local ingest:
  - normal CLI/Make ingest requires a clean vault Git worktree before writing
    files;
  - successful ingest stages and commits the generated source record and
    knowledge note paths;
  - archive binaries and full evidence artifacts remain outside the vault and
    are not staged;
  - `slack-vault ingest-file --no-git-commit` and
    `make ingest-file FILE=... NO_GIT_COMMIT=1` keep local development runs from
    committing.
- Phase 4 was live-smoke-tested with two real DOCX files in local archive mode:
  - `BIZEE Product-Centralized Operating Model.docx` produced source
    `source-2026-06-13-a0a9730a88b0`, a knowledge note, and vault commit
    `a66be4e Ingest source-2026-06-13-a0a9730a88b0`;
  - `Hubspot - Current State.docx` produced source
    `source-2026-06-13-b7b566e4c0e9`, a knowledge note, and vault commit
    `c9a1898 Ingest source-2026-06-13-b7b566e4c0e9`;
  - both vault commits were pushed to the Obsidian vault repository.
- Phase 5 Local Retrieval and Q&A is implemented as a local-only slice:
  - `src/slack_vault/retrieval.py` loads generated knowledge notes and source
    records from the configured vault, including frontmatter, Markdown body,
    source IDs, relative paths, and source-record provenance;
  - Obsidian CLI search (`obsidian search ... format=json`) supplies the vault
    hit paths, so search semantics stay anchored in Obsidian rather than an
    app-owned index;
  - citation-aware answer context includes note paths, note titles, source IDs,
    source-record paths, matched terms, scores, and excerpts used for answers;
  - `src/slack_vault/qa.py` defines the answer prompt contract, Anthropic-backed
    answer generation, strict JSON parsing, Markdown-style citation rendering,
    and deterministic no-evidence responses when retrieval finds no useful
    context;
  - `slack-vault ask "question"` and `make ask QUESTION="question"` provide the
    local Q&A entrypoint.
- The Obsidian CLI vault name can be overridden with
  `SLACK_VAULT_OBSIDIAN_CLI_VAULT`; otherwise the configured vault directory
  name is used.
- Phase 5 was smoke-tested against the current DOCX-derived vault notes:
  `Hubspot - Current State.docx` answered with a cited knowledge note and source
  record for `source-2026-06-13-b7b566e4c0e9`.
- Local POC reset tooling is available for repeated smoke-test loops:
  `slack-vault clean-poc-data --yes` and `make clean-poc-data` remove generated
  knowledge notes, generated source records, and the local archive path while
  leaving input files such as sample DOCX documents in place.
- Latest pushed code repository commit:
  `9bed310 Implement synthesis and vault git integration`.
- Latest pushed Obsidian vault repository commit:
  `c9a1898 Ingest source-2026-06-13-b7b566e4c0e9`.
- Both repositories are clean and tracking `origin/main`.
- The code repository is configured with `uv`, `ruff`, `mypy`, `pytest`,
  `pytest-cov`, `pre-commit`, `AGENTS.md`, and split Make targets under
  `makefiles/`.
- `make check` passes with no external services configured:
  `111 passed, 2 skipped`, total coverage `90.90%`.
- Opt-in live Anthropic tests are available and passed locally with
  `SLACK_VAULT_RUN_LIVE_AI_TESTS=1`:
  - `uv run pytest tests/test_ai.py -k live -q --no-cov`;
  - `uv run pytest tests/test_enhancement.py -k live -q --no-cov`.
- The Obsidian vault repository opens as a vault and keeps local `.obsidian/`
  app state ignored.

Next implementation phase: Phase 6 Slack Ingestion POC.

Immediate next steps:

1. Wire Slack ingestion to the existing archive, extraction, optional
   enhancement, synthesis, source-record, and Git-commit pipeline.
2. Keep Slack Q&A, vector retrieval, and shared deployment out of Phase 6.

Broader synthesis quality tuning, richer note-update behavior, and repeated
live-document hardening are intentionally tracked later in Phase 10 so the POC
can first complete the full ingestion-to-question-answering loop.

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
- CLI path that archives a local file, stores extracted evidence outside the
  vault, and writes extraction provenance into the source record.
- Tests using small committed fixtures or programmatically generated fixtures.

#### Acceptance Criteria

- Sample Markdown, text, PDF, Word, and Excel files produce readable extracted
  evidence.
- Source records show extraction status, evidence counts, and the outside-vault
  evidence artifact pointer.
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
  archived copy, writes full evidence JSON outside the vault, writes extraction
  status and artifact metadata into the source record, and prints extraction
  status in CLI output.
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

- `AITextProvider` and `AIFileProvider` interfaces plus Anthropic implementation
  for enhancement prompts and original-file inspection.
- Prompt caching support in the provider request/response model before serious
  enhancement prompt work:
  - opt-in automatic caching with top-level `cache_control`;
  - explicit cache breakpoints on stable system/document content where needed;
  - cache usage fields such as `cache_creation_input_tokens` and
    `cache_read_input_tokens`;
  - tests proving cache parameters are sent and cache usage is captured.
- `EvidenceEnhancer` interface that accepts deterministic evidence and returns
  enhanced evidence while preserving source anchors.
- Enhancement status updates in source records, separate from extraction status.
- AI-assisted preprocessing prompts for:
  - cleaning noisy PDF text;
  - inferring sections from weak layout;
  - interpreting tables while preserving cited source locations;
  - summarizing large evidence blocks without replacing the original evidence.
- Mocked tests for prompt input/output parsing and failure handling.
- Opt-in live Anthropic smoke test gated by `SLACK_VAULT_RUN_LIVE_AI_TESTS=1`,
  including file upload, file-grounded message, and file cleanup.

#### Implementation Notes

Phase 2b is implemented in two layers: provider harness groundwork and opt-in
AI evidence enhancement.

- `src/slack_vault/config.py` loads `.env` from the current working directory for
  default CLI/settings usage. Real environment variables override matching
  `.env` values.
- `src/slack_vault/ai.py` defines:
  - `AITextProvider`;
  - `AIFileProvider`;
  - `AITextRequest`;
  - `AITextResponse`;
  - `AIUploadedFile`;
  - `AnthropicAIProvider`.
- `AnthropicAIProvider` supports:
  - text completions through the Anthropic Messages API;
  - file upload through `client.beta.files.upload`;
  - file-grounded beta Messages requests using uploaded `file_id` document
    blocks;
  - uploaded-file deletion through `client.beta.files.delete`.
- Prompt caching is optional and request/provider scoped:
  - `AIPromptCacheConfig` controls TTL, automatic top-level caching, system
    prompt breakpoints, and uploaded-file breakpoints;
  - text requests pass Anthropic `cache_control` when caching is enabled;
  - file-grounded beta requests can cache uploaded document blocks;
  - `AITextResponse` captures `cache_creation_input_tokens` and
    `cache_read_input_tokens`.
- `src/slack_vault/enhancement.py` defines the Phase 2b enhancement surface:
  - `EvidenceEnhancer` protocol;
  - `EnhancementStatus`;
  - `EnhancementResult`;
  - `EnhancedEvidenceBlock`;
  - `AnthropicEvidenceEnhancer`.
- `AnthropicEvidenceEnhancer` accepts deterministic `ExtractionResult` values,
  skips failed or empty extraction results, prompts Anthropic for JSON with
  `enhanced_text`, records invalid AI responses as failed enhancement results,
  and preserves each block's deterministic source sequence and location.
- `src/slack_vault/source_registry.py` writes enhancement metadata to
  frontmatter and links to the outside-vault evidence artifact rather than
  embedding full enhanced evidence in the vault.
- `src/slack_vault/ingest.py` accepts an optional `EvidenceEnhancer`. When it is
  absent, deterministic ingest still works and source records show
  `enhancement_status: "not_requested"`.
- `slack-vault ingest-file --enhance` and
  `make ingest-file FILE=path/to/source.md ENHANCE=1` opt into live Anthropic
  enhancement for local ingest.
- `tests/test_ai.py` covers the Anthropic harness with mocked text, upload,
  file-grounded message, prompt caching, and delete paths.
- `tests/test_enhancement.py` covers mocked enhancement parsing, source-anchor
  preservation, skipped extraction, invalid AI responses, and an opt-in live
  enhancement ingest smoke test.
- `tests/test_ingest.py`, `tests/test_source_registry.py`, and
  `tests/test_cli.py` cover optional enhancement wiring and source-record output.
- The live Anthropic smoke test uses the key from `.env`, verifies text
  completion, uploads a tiny temporary file, asks a file-grounded question, and
  deletes the uploaded file.
- The live Phase 2b enhancement smoke test creates a temporary Markdown source,
  runs archive, deterministic extraction, Anthropic enhancement, evidence
  artifact writing, and source record writing, then verifies both extracted and
  enhanced evidence are preserved outside the vault.

Validation:

- `make check` passes with 42 tests, two skipped live tests, and 90.57%
  coverage.
- `SLACK_VAULT_RUN_LIVE_AI_TESTS=1 uv run pytest tests/test_ai.py -k live -q --no-cov`
  passes with one live test.
- `SLACK_VAULT_RUN_LIVE_AI_TESTS=1 uv run pytest tests/test_enhancement.py -k live -q --no-cov`
  passes with one live test.

#### Restart Context

- Normal local ingest remains deterministic and offline:
  `make ingest-file FILE=path/to/source.md`.
- AI-enhanced ingest is explicit and uses the configured Anthropic API key:
  `make ingest-file FILE=path/to/source.md ENHANCE=1`.
- Source records now always include enhancement status. Without enhancement,
  status is `not_requested`; with successful enhancement, status is `completed`.
- Phase 2b failures should not block Phase 2a extraction. Enhancement failures
  are recorded separately from extraction failures.
- Classification, taxonomy selection, note matching, knowledge-note writing,
  retrieval, and Slack Q&A are still future work.

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
- Mocked tests cover updating the same knowledge note when a strong match is
  returned.
- Generated knowledge notes include frontmatter, backlinks, source citations,
  and a readable Markdown body.
- The generated vault remains usable in Obsidian.
- Tests cover prompt input/output parsing with mocked AI responses.
- Live related-document update behavior, note-splitting policy, citation
  quality, and human-edit preservation are deferred to Phase 10 after the full
  round-trip POC exists.

### Initial Implementation Notes

The first Phase 3 slice is implemented without Slack or Git commit integration.

- `src/slack_vault/synthesis.py` defines the synthesis interface, Anthropic
  implementation, source classification metadata, citation model, existing-note
  reader, knowledge-note writer, and mocked-testable JSON prompt contract.
- `slack-vault ingest-file --synthesize` runs archive, extraction, optional
  enhancement, outside-vault evidence artifact writing, knowledge synthesis,
  source-record writing, then optional Git commit.
- `make ingest-file FILE=path/to/source.md SYNTHESIZE=1` provides the Make
  wrapper. `ENHANCE=1 SYNTHESIZE=1` uses enhanced evidence for synthesis when
  enhancement succeeds.
- Failed requested synthesis fails the ingest before vault source-record writing
  and Git commit. Skipped synthesis, such as no extracted evidence, can still
  create a source record for auditability.
- `tests/test_synthesis.py` covers mocked AI create/update decisions, weak-match
  new-note creation, enhanced-evidence selection, invalid AI responses, skipped
  synthesis, and existing-note scanning.

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

### Implementation Notes

Phase 4 is implemented for the local ingest path.

- `src/slack_vault/git_vault.py` detects the vault Git worktree, enforces a
  clean worktree before commit-mode ingest, stages only generated vault paths,
  builds structured ingest commit messages, and commits through Git.
- CLI ingest commits by default. `--no-git-commit` is the local development mode
  for writing files without committing.
- The Make wrapper exposes the same behavior with `NO_GIT_COMMIT=1`.
- Dirty vault repositories fail before archive/extraction/write work begins, so
  an ingest does not overwrite pending human or previous generated changes.
- Requested AI enhancement or synthesis failures fail the ingest before writing
  a vault source record or creating a vault Git commit. This prevents a
  rate-limited synthesis run from committing only the source record and looking
  like a successful full ingest.
- Tests cover commit message generation, successful temp-repo commits,
  no-change skipped commits, non-Git vault errors, dirty-vault errors, ingest
  orchestration, failed requested AI stages, and CLI commit/no-commit behavior.

## 9. Phase 5: Local Retrieval And Q&A

### Goal

Implement question answering over the vault before adding Slack Q&A.

### Deliverables

- Vault search interface.
- Frontmatter metadata search.
- Obsidian CLI search over vault Markdown content.
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
- Tests cover Obsidian CLI search-hit mapping and answer prompt assembly with
  mocked AI responses.

### Completion Notes

Phase 5 is implemented as a local-only slice. It does not add Slack handlers,
queues, or shared deployment state.

- `src/slack_vault/retrieval.py` includes:
  - a vault note model for knowledge notes and source records;
  - Markdown/frontmatter loading from the configured vault;
  - Obsidian CLI search over the configured vault;
  - source-ID and source-record lookup helpers.
- The answer-context builder returns compact grounded snippets with:
  - note path;
  - note title;
  - source IDs;
  - source-record paths when available;
  - excerpt text used for the answer.
- `src/slack_vault/qa.py` includes:
  - an answer-generation prompt contract;
  - an Anthropic-backed local answerer using the existing retrying AI text
    provider;
  - a deterministic no-evidence response when retrieval finds no useful
    context.
- CLI support includes:
  - `slack-vault ask "question"` for local Q&A;
  - `make ask QUESTION="question"` wrapper;
  - output that includes the answer and Markdown-style citations.
- Tests cover:
  - frontmatter/body loading;
  - Obsidian CLI command construction and result parsing;
  - Obsidian search-hit mapping from knowledge notes and source records;
  - citation-context construction;
  - no-evidence behavior;
  - mocked AI prompt assembly and answer parsing;
  - CLI behavior with a temporary vault fixture.
- One live local Q&A smoke test passed against the current DOCX-derived vault
  notes. Original source documents and full evidence artifacts remain outside
  Git.

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

## 14. Phase 10: Knowledge Synthesis POC Hardening

### Goal

Return to Phase 3 synthesis after the full local-to-Slack-to-Q&A loop works, and
harden note quality, update behavior, and citation usefulness using real POC
usage feedback.

This phase intentionally happens after the first round-trip pipeline exists so
the synthesis hardening work is grounded in actual documents, real retrieval
needs, and Slack Q&A behavior rather than speculative prompt polishing.

### Deliverables

- Live related-document update smoke tests.
- Note update strategy that preserves:
  - original `created_at`;
  - source history;
  - existing source citations;
  - future human edits where practical.
- Clear large-document note policy:
  - single note;
  - multiple section/domain notes;
  - map/index note plus child notes.
- Citation quality improvements:
  - tighter inline citations where helpful;
  - source-record backlinks;
  - evidence artifact references when deeper audit is needed.
- Prompt versioning for synthesis prompts.
- Regression fixtures from representative POC documents.
- Taxonomy consistency pass based on the notes produced during POC usage.
- Reprocess/regenerate workflow for improving existing generated notes without
  re-archiving the original source.

### Acceptance Criteria

- A second related live source updates the appropriate existing note without
  losing prior source history.
- Existing human edits are preserved or conflicts are surfaced clearly.
- Large documents follow a documented split-or-single-note policy.
- Generated citations are useful for both human reading and future Q&A
  grounding.
- Representative POC documents have regression fixtures or saved expected-shape
  checks.
- The Git-backed vault still contains only Markdown knowledge artifacts and
  lightweight source records, with no original binaries or full evidence dumps.

## 15. Deferred Until After POC

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

## 16. Resolved And Remaining Early Decisions

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
  Obsidian CLI-backed Q&A loop. Decision: vector retrieval can follow the local
  Obsidian search and Q&A path.
