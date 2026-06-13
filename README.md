# Slack Vault

Slack Vault is the app/tooling repository for a Slack-to-Obsidian knowledge
base. The generated Obsidian vault lives in a separate Git repository so people
can clone and open it directly in Obsidian without running the backend.

Current local defaults:

- App/tooling repository: `/Users/utpalrohan/code/slack_vault`
- Obsidian vault repository: `/Users/utpalrohan/code/slack_obsidian`
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
record in the configured vault:

```sh
make ingest-file FILE=path/to/source.md
```

The initial local extractor supports Markdown, plain text, PDF, DOCX, and XLSX
sources. Source records include extraction status and source-grounded evidence
anchors such as headings, PDF pages, Word paragraphs or tables, and spreadsheet
sheets or cell ranges.

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

Original source files should be archived outside the Obsidian vault and outside
Git. The local archive path defaults to `.data/archive`.
