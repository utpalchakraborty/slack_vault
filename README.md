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

To initialize or refresh the starter Obsidian vault structure:

```sh
set -a
source .env
set +a
make init-vault
```

To inspect resolved settings:

```sh
make show-config
```

To archive a local file and create a source record in the configured vault:

```sh
make ingest-file FILE=path/to/source.md
```

## Development Checks

```sh
make format
make check
```

`pytest` is configured to fail below 90% coverage.

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
