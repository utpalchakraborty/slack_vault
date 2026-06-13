# Slack Vault

Slack Vault is the app/tooling repository for a Slack-to-Obsidian knowledge
base. The generated Obsidian vault lives in a separate Git repository so people
can clone and open it directly in Obsidian without running the backend.

Current local defaults:

- App/tooling repository: `/Users/utpalrohan/code/slack_vault`
- Obsidian vault repository: `/Users/utpalrohan/code/slack_obsidian`
- Log file: `.data/logs/slack-vault.log`
- AI provider: Anthropic
- AI model: `claude-haiku-4-5-20251001`

## Local Setup

```sh
make install
cp .env.example .env
make pre-commit-install
```

Add your real Slack and Anthropic credentials to `.env`. Do not commit `.env`.
CLI commands load `.env` from the current working directory automatically, and
real environment variables override matching values from `.env`.

To initialize or refresh the starter Obsidian vault structure:

```sh
make init-vault
```

To inspect resolved settings:

```sh
make show-config
```

To archive a local file, extract deterministic evidence, and create a source
record in the configured vault, then commit the vault changes to Git:

```sh
make ingest-file FILE=path/to/source.md
```

The initial local extractor supports Markdown, plain text, PDF, DOCX, and XLSX
sources. Source records in the vault include provenance, extraction status,
evidence counts, and a pointer to the full evidence artifact. Full extracted
evidence is stored outside the Git-backed vault under the configured archive
path.

Commit mode requires the configured vault repository to be clean before ingest
starts. This keeps one ingest mapped to one vault commit. For local scratch runs
that should write files without committing, set `NO_GIT_COMMIT=1`:

```sh
make ingest-file FILE=path/to/source.md NO_GIT_COMMIT=1
```

To reset local POC generated data before another smoke-test loop, run:

```sh
make clean-poc-data
```

This removes generated knowledge notes, generated source records, and the local
archive path. It does not remove local input files such as sample DOCX files.

To opt into AI evidence enhancement for a local ingest, set `ENHANCE=1`. This
uses the configured Anthropic model and API key, preserves the deterministic
evidence, and stores enhanced evidence in the outside-vault evidence artifact.

```sh
make ingest-file FILE=path/to/source.md ENHANCE=1
```

To opt into Phase 3 AI classification and knowledge-note synthesis, set
`SYNTHESIZE=1`. This archives the source, extracts evidence, writes the source
evidence artifact outside the vault, asks the configured Anthropic model to
create or update a Markdown note under `10 Knowledge/`, then writes the source
record and commits the generated vault files.

```sh
make ingest-file FILE=path/to/source.md SYNTHESIZE=1
```

Enhancement and synthesis can be combined when useful:

```sh
make ingest-file FILE=path/to/source.md ENHANCE=1 SYNTHESIZE=1
```

AI-backed ingest retries transient provider failures, including Anthropic rate
limits, before failing the ingest. If requested enhancement or synthesis still
fails, the command exits non-zero before writing a vault source record or
creating a Git commit. Retry behavior can be changed with
`SLACK_VAULT_AI_RETRY_MAX_ATTEMPTS`,
`SLACK_VAULT_AI_RETRY_INITIAL_DELAY_SECONDS`,
`SLACK_VAULT_AI_RETRY_MAX_DELAY_SECONDS`, and
`SLACK_VAULT_AI_RETRY_BACKOFF_MULTIPLIER`.

Ingest commands log progress to stderr and to the configured log file. By
default logs rotate every day at midnight, rotated logs are gzip-compressed, and
the most recent 14 rotated files are retained. The path, level, and retention
can be changed with `SLACK_VAULT_LOG_PATH`, `SLACK_VAULT_LOG_LEVEL`, and
`SLACK_VAULT_LOG_BACKUP_COUNT`.

Sequential automatic ingest flows use
`SLACK_VAULT_AUTOMATIC_INGEST_DELAY_SECONDS` as the delay between documents.
The default is 75 seconds so later Slack batch ingestion does not immediately
hit token-per-minute limits after processing a large document.

## Development Checks

```sh
make format
make check
```

`pytest` is configured to fail below 90% coverage.

Live Anthropic smoke tests are skipped by default. To run the text, file upload,
file-grounded message, and file cleanup checks intentionally against the API key
in `.env`:

```sh
SLACK_VAULT_RUN_LIVE_AI_TESTS=1 uv run pytest tests/test_ai.py -k live -q --no-cov
```

To run the live Phase 2b enhancement ingest smoke test:

```sh
SLACK_VAULT_RUN_LIVE_AI_TESTS=1 uv run pytest tests/test_enhancement.py -k live -q --no-cov
```

## Make Targets

The root `Makefile` includes targets split by concern:

- `makefiles/dev.mk` contains setup, formatting, linting, typing, and test
  targets.
- `makefiles/run.mk` contains runtime CLI targets.

Run `make help` for the current target list.

## Repository Layout

```text
AGENTS.md            Agent and contributor guidance
docs/                 Design and implementation plan
makefiles/            Included Make target groups
src/slack_vault/      Application package
tests/                Unit tests
```

Original source files and full extracted evidence artifacts should be archived
outside the Obsidian vault and outside Git. The local archive path defaults to
`.data/archive`.
