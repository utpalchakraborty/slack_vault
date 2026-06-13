# AGENTS.md

## Project Boundaries

- This repository, `/Users/utpalrohan/code/slack_vault`, contains the Slack
  Vault application code, tests, docs, and developer tooling.
- The Obsidian vault repository is separate at
  `/Users/utpalrohan/code/slack_obsidian`.
- Do not store original uploaded source files in either Git repository. Source
  files belong in the configured archive provider.
- The current AI provider default is Anthropic with model
  `claude-haiku-4-5-20251001`.

## Development Workflow

- Use `uv` for dependency management and command execution.
- Prefer Make targets over spelling out long `uv run` commands:
  - `make install` installs development dependencies.
  - `make dev` installs dependencies and pre-commit hooks.
  - `make check` runs formatting checks, linting, mypy, and tests.
  - `make init-vault` initializes the configured Obsidian vault path.
  - `make ingest-file FILE=...` archives a local file and writes a source
    record.
- Keep implementation changes scoped to `src/slack_vault/` and matching tests
  under `tests/`.
- Keep docs aligned with code when changing configuration, commands, or repo
  boundaries.

## Quality Bar

- Python code should pass `ruff`, `mypy --strict`, and `pytest`.
- Test coverage must stay at or above the configured 90% threshold.
- Do not commit `.env`, local archives, virtualenvs, coverage output, or cache
  directories.
- Generated Obsidian vault content should remain readable as plain Markdown.
