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

To run the vault connection agent after synthesis, add `CONNECT=1`. This uses
the Claude Agent SDK with the configured Obsidian and Slack Vault skills, lets
the agent edit vault Markdown, validates the resulting diff, then includes the
accepted connection paths in the same vault commit.

```sh
make ingest-file FILE=path/to/source.md SYNTHESIZE=1 CONNECT=1
```

To connect an already synthesized note, run the backfill command. If the note
has exactly one `source_ids` entry in frontmatter, the source ID is inferred;
otherwise pass `SOURCE_ID=...`.

```sh
make connect-note NOTE="10 Knowledge/example.md"
make connect-note NOTE="10 Knowledge/example.md" SOURCE_ID=source-YYYY-MM-DD-abcdef123456
```

Enhancement and synthesis can be combined when useful:

```sh
make ingest-file FILE=path/to/source.md ENHANCE=1 SYNTHESIZE=1
```

To ask a local question against generated vault notes:

```sh
make ask QUESTION="What does the vault say about the operating model?"
```

Answers first ask the configured Anthropic model to plan concise Obsidian search
queries, run those searches over the configured vault, then ask Anthropic to
synthesize only from the returned vault search hits. Output includes
Markdown-style citations back to vault notes and source records. If no relevant
context is found, the command returns a deterministic no-evidence answer after
the search-planning step.

The Obsidian desktop app must be running and its CLI must be registered on
`PATH`. Enable it in Obsidian Settings > General > Command line interface. The
CLI vault name defaults to the configured vault directory name and can be
overridden with `SLACK_VAULT_OBSIDIAN_CLI_VAULT`.

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

AI provider interactions are also written to rotating JSONL for later prompt and
answer tuning. The default path is `.data/logs/ai-interactions.jsonl`,
configurable via `SLACK_VAULT_AI_INTERACTION_LOG_PATH`. It rotates daily,
gzip-compresses rotated files, and uses the same `SLACK_VAULT_LOG_BACKUP_COUNT`
retention setting as the app log. These records include full prompts and model
responses, so keep them outside Git and treat them as sensitive runtime data.

Sequential automatic ingest flows use
`SLACK_VAULT_AUTOMATIC_INGEST_DELAY_SECONDS` as the delay between documents.
The default is 75 seconds so later Slack batch ingestion does not immediately
hit token-per-minute limits after processing a large document.

Vault connection work is being added behind explicit settings. Custom
Slack Vault agent skills live in the Obsidian vault under
`90 System/agent-skills/slack-vault`, and upstream Obsidian skills are expected
under `90 System/agent-skills/upstream/obsidian-skills`. The paths default to
being resolved relative to `SLACK_VAULT_OBSIDIAN_PATH` and can be changed with
`SLACK_VAULT_CUSTOM_SKILLS_PATH` and `SLACK_VAULT_OBSIDIAN_SKILLS_PATH`.

Before an agent-produced connection diff is committed, it can be inspected with:

```sh
make validate-vault-diff SOURCE_ID=source-YYYY-MM-DD-abcdef123456 PRIMARY_NOTE="10 Knowledge/example.md"
```

The validator allows only bounded Markdown changes in expected vault folders,
rejects protected paths such as `.obsidian/` and `90 System/agent-skills/`, and
checks that the source record and primary note still reference the source ID.

To run the Phase 6 Slack ingestion POC locally, configure the Slack values in
`.env`, then verify credentials, channel access, app manifest settings, and
Socket Mode readiness:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `SLACK_VAULT_APP_ID`
- `SLACK_APP_CONFIG_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SLACK_VAULT_TEAM_ID`
- `SLACK_VAULT_INGESTION_CHANNEL_ID`
- `SLACK_VAULT_INGESTION_CHANNEL_NAME`

`SLACK_APP_CONFIG_TOKEN` is used only by `check-slack-setup` to export the app
manifest and verify Socket Mode, bot event subscriptions, and configured bot
scopes. It is not used by the listener or ingestion worker.

```sh
make check-slack-setup
```

If the setup check passes, start the Socket Mode listener. The listener records
new Slack upload events and starts one background `make slack-worker ONCE=1`
process whenever a new ingestion job is queued:

```sh
make run-slack
```

For manual recovery or debugging, process any currently queued Slack ingestion
jobs:

```sh
make slack-worker
```

For a single manual job attempt during development, use:

```sh
make slack-worker ONCE=1
```

The listener records Slack events and queues jobs in
`SLACK_VAULT_OPERATIONAL_DB_PATH`. The worker downloads Slack files to local
temporary storage outside Git, then runs the same archive, extraction, optional
enhancement, optional synthesis, source-record, and Git-commit pipeline used by
local file ingest. Slack ingestion also pushes successful vault Git commits to
the configured upstream by default so other Obsidian vault clones can pull the
new notes. Set `SLACK_VAULT_SLACK_INGEST_GIT_PUSH=false` to keep Slack-ingested
vault commits local during development.

Slack connection is disabled by default while the agent flow is being hardened.
Enable it with `SLACK_VAULT_SLACK_INGEST_CONNECT=true` after synthesis is also
enabled. Connection limits are controlled by
`SLACK_VAULT_CONNECTION_MAX_TURNS`,
`SLACK_VAULT_CONNECTION_MAX_TOUCHED_PATHS`, and
`SLACK_VAULT_CONNECTION_MAX_CHANGED_LINES`.

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
