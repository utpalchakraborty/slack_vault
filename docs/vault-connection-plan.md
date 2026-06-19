# Vault Connection Plan

This document plans the next ingestion enhancement: after a document is imported
and synthesized, Slack Vault should connect the resulting Markdown into the
existing Obsidian vault graph. The feature should make imported documents useful
as part of a linked knowledgebase rather than leaving each import as an isolated
note.

## Current Status

Updated: 2026-06-19.

The first implementation pass has landed and has been pushed to both repos.

Slack Vault app repo:

- `01751e8` added vault connection diff inspection and validation.
- `6719eb6` threaded optional vault connection through local ingest and Git
  commit staging.
- `af5a4dd` added the Claude Agent SDK-backed vault connector and Slack ingest
  wiring.
- `8ccfd71` added the `connect-note` backfill command and dedicated connection
  commit messages.

Obsidian vault repo:

- `950f68d` added the upstream Obsidian skills submodule and initial
  Slack Vault custom skills under `90 System/agent-skills/`.

Current working state:

- local ingest supports `SYNTHESIZE=1 CONNECT=1`;
- Slack ingest can create the connector when
  `SLACK_VAULT_SLACK_INGEST_CONNECT=true`;
- existing notes can be connected with `slack-vault connect-note` or
  `make connect-note`;
- connection diffs are inspected and validated before Git commit;
- normal agent runs are blocked from touching `.obsidian/`, `.git/`, skill
  files, non-Markdown files, unsafe paths, deletes, renames, and overly large
  diffs;
- `make check` passed with 207 tests, 2 skipped, and 90.15 percent coverage.

Not done yet:

- live Claude Agent smoke test against a disposable or approved vault note;
- Slack live smoke test with connection enabled;
- full raw agent-message artifact logging outside Git;
- batch backfill command for many existing notes;
- prompt/skill tuning based on real vault diffs.

## 1. Goal

When a source document is ingested, the system should:

1. Preserve the original file in the archive provider.
2. Extract evidence and write the source record.
3. Create or update the primary knowledge note.
4. Run an agent over the Obsidian vault to discover useful connections.
5. Let the agent edit relevant vault Markdown directly.
6. Inspect and validate the resulting Git diff.
7. Commit and push the accepted vault changes.

The important design choice is that the agent may edit Markdown files in the
Obsidian vault, but Slack Vault remains responsible for orchestration,
validation, commit, push, and failure reporting.

## 2. Non-Goals

- Do not store original uploaded source files in either Git repository.
- Do not create an app-owned canonical graph database. Obsidian Markdown and
  Git remain the source of truth.
- Do not require human review before every vault update.
- Do not allow the agent to commit, push, reset, clean, or rewrite Git history.
- Do not let agent runs modify local Obsidian app state, archives, runtime
  databases, logs, or downloaded Slack files.
- Do not introduce a broad ontology before we have stable connection behavior.

## 3. Desired User Experience

For local ingest:

```sh
make ingest-file FILE=path/to/source.docx SYNTHESIZE=1 CONNECT=1
```

Expected result:

- source record is written under `20 Sources/sources/`;
- primary knowledge note is created or updated under `10 Knowledge/`;
- related notes, map files, or reciprocal links are updated as needed;
- one vault Git commit contains all accepted vault Markdown changes;
- CLI output reports the source, main note, connected files, and commit hash.

For Slack ingest:

- a file uploaded to the ingestion channel follows the same pipeline;
- the Slack thread reply reports the source record, primary note, connected
  notes/maps, commit hash, and failure details if validation blocked the commit.

## 4. End-To-End Flow

### Step 1: Start From A Clean Vault

Before any write, the configured vault Git worktree must be clean. The current
commit model already enforces this for Git-backed ingest. The connection stage
should use the same precondition.

Acceptance criteria:

- connection mode refuses to start when the vault has uncommitted changes;
- the failure tells the operator which vault paths are dirty;
- local development can still bypass commit mode with existing no-commit flags
  when explicitly requested.

### Step 2: Run The Existing Ingest Pipeline

The existing pipeline remains the front half of the workflow:

1. archive original file;
2. extract deterministic evidence;
3. optionally enhance evidence;
4. optionally synthesize the primary knowledge note;
5. write the source record.

Connection requires a synthesized knowledge note. If synthesis is not requested
or is skipped because no evidence exists, connection should be skipped with a
clear status.

Acceptance criteria:

- deterministic-only ingest behavior remains unchanged by default;
- `CONNECT=1` without synthesis fails or skips explicitly rather than trying to
  connect a source record alone;
- source-record and knowledge-note writes happen before the agent starts.

### Step 3: Launch The Vault Connection Agent

Use the Claude Agent SDK for the connection stage. The SDK gives the agent
filesystem tools, shell access, local plugin loading, and filesystem skills. It
is a better fit for multi-step vault navigation than a single text-completion
prompt.

The agent prompt should include:

- vault path;
- source ID;
- source record path;
- primary knowledge note path;
- allowed folders;
- connection rules;
- required final summary format;
- explicit prohibition on Git commands and non-vault writes.

The agent should have a bounded turn budget and should run with plugin paths for
upstream Obsidian skills plus Slack Vault custom skills.

Acceptance criteria:

- SDK runner can be unit-tested behind a protocol/fake runner;
- live SDK use is isolated behind an opt-in local smoke test;
- agent stdout/messages and summary are captured in logs or an outside-Git
  artifact for later prompt tuning.

### Step 4: Agent Discovers Connections With Skills

The agent should use skills to:

1. read the primary note and source record;
2. search the vault for title terms, topics, taxonomy, named entities, tools,
   customers, projects, teams, and source filenames;
3. inspect top candidate notes and backlinks;
4. read relevant map/index files;
5. decide which connections are useful;
6. edit Markdown files directly.

Initial connection actions should be conservative:

- add wikilinks inside the new/updated note where the text already mentions an
  existing concept;
- add or update a `## Related Notes` section;
- update `30 Maps/*-index.md` files with links to the note;
- add reciprocal links only when the relationship is obvious;
- add frontmatter aliases/tags only when they follow existing conventions;
- recommend bridge notes in the final summary, but do not create them
  automatically in the first implementation.

Acceptance criteria:

- agent runs make useful links without flooding the note;
- connection edits preserve existing source citations;
- map files remain readable plain Markdown;
- no facts are added unless grounded in the source note or existing vault notes.

### Step 5: Inspect The Git Diff

After the agent exits, Slack Vault inspects the vault Git diff. The diff is the
source of truth for what the agent actually changed.

The inspection should collect:

- changed paths;
- added paths;
- deleted paths;
- renamed paths;
- diff stats;
- inserted/deleted line counts;
- whether any path is outside allowed folders;
- whether any non-Markdown file changed.

Acceptance criteria:

- no commit is attempted without a non-empty validated diff;
- diff inspection works without relying on the agent's self-reported summary;
- the connection result records every touched path.

### Step 6: Validate The Diff

The validator should allow only expected vault Markdown changes.

Allowed paths:

- `10 Knowledge/**/*.md`
- `20 Sources/sources/**/*.md`
- `30 Maps/**/*.md`
- selected `90 System/**/*.md` only if explicitly enabled later

Forbidden paths:

- original source files;
- archive paths;
- `.obsidian/**`;
- `.git/**`;
- runtime logs;
- SQLite databases;
- Slack downloads;
- binary files;
- files outside the configured vault path;
- deletes, unless a later migration mode explicitly enables them.

Validation checks:

1. The vault worktree was clean before the run.
2. Only allowlisted paths changed.
3. Only Markdown files changed.
4. No unexpected deletes or renames happened.
5. Total touched paths and line counts stay under configured bounds.
6. Wikilinks do not introduce unresolved targets, except for explicitly allowed
   plain-text forward references.
7. The source record for the current source ID remains present.
8. The primary knowledge note remains present and still references the source
   ID.
9. Agent-added prose does not remove the `## Sources` section or source
   citations.

Acceptance criteria:

- validation failure leaves the vault diff uncommitted for inspection;
- failure output names the exact rule and path that failed;
- successful validation returns the exact paths to stage.

### Step 7: Commit And Push

Once validation passes, Slack Vault stages only the validated paths, commits, and
optionally pushes. The agent must not run Git commit or push itself.

Commit message shape:

```text
Connect source-YYYY-MM-DD-abcdef123456

Source ID: source-YYYY-MM-DD-abcdef123456
Source filename: Example.docx
Source record: 20 Sources/sources/source-YYYY-MM-DD-abcdef123456.md
Primary knowledge note: 10 Knowledge/example.md

Connected vault paths:
- 10 Knowledge/example.md
- 10 Knowledge/related-note.md
- 30 Maps/topic-index.md
```

Acceptance criteria:

- only validated paths are staged;
- no unrelated vault changes are committed;
- push behavior follows the existing local/Slack Git-push settings;
- skipped commits are reported when the diff is empty after validation.

### Step 8: Report Results

The result should be visible in CLI output, Slack replies, logs, and operational
state where applicable.

Report:

- connection status: not requested, skipped, completed, failed validation, agent
  failed, commit skipped;
- source ID;
- primary note path;
- touched paths;
- commit hash;
- push status;
- validation errors;
- short agent summary.

Acceptance criteria:

- local CLI output is enough to debug a failed connection run;
- Slack worker records source ID, note paths, connection status, and commit hash;
- AI interaction and agent logs remain outside Git and rotate like existing logs
  where practical.

## 5. Skill Organization

Skills should live with the Obsidian vault, not primarily in the Slack Vault app
repository. The app repository should provide orchestration code, validation,
tests, and defaults for finding skills; the skill content itself is part of the
vault repo because it is vault-specific operating knowledge.

Recommended vault layout:

```text
/Users/utpalrohan/code/slack_obsidian/
  90 System/
    agent-skills/
      upstream/
        obsidian-skills/      # kepano/obsidian-skills checkout or submodule
      slack-vault/
        .claude-plugin/
          plugin.json
        skills/
          connect-imported-document/
            SKILL.md
          verify-vault-links/
            SKILL.md
          update-vault-maps/
            SKILL.md
          detect-duplicate-or-merge-target/
            SKILL.md
```

The connection agent may read these skill files, but normal connection runs
should not modify them. Diff validation should reject changes under
`90 System/agent-skills/**` unless the operator is explicitly running a skill
maintenance command.

### Upstream Obsidian Skills

Use `kepano/obsidian-skills` as an upstream skill source inside the Obsidian
vault repo. Keep it as close to upstream as possible so it can be updated
deliberately.

Recommended repo layout:

```text
90 System/
  agent-skills/
    upstream/
      obsidian-skills/    # pinned upstream checkout or submodule
```

The first useful upstream skills are:

- `obsidian-markdown`
- `obsidian-cli`
- `obsidian-bases`
- `json-canvas`

Implementation decision to finalize:

- Prefer a Git submodule for exact upstream tracking. Updating means pulling the
  upstream repo, reviewing any skill changes, and committing the updated
  submodule pointer in the Obsidian vault repo.
- If submodule ergonomics are too annoying for Obsidian users, vendor a copied
  snapshot instead and add a Make target that refreshes it from GitHub.

### Slack Vault Custom Skills

Keep Slack Vault-specific agent behavior in the Obsidian vault repository as
normal committed files. These skills are not generic app code; they encode how
this vault should organize imported knowledge.

Recommended layout:

```text
90 System/
  agent-skills/
    slack-vault/
      .claude-plugin/
        plugin.json
      skills/
        connect-imported-document/
          SKILL.md
        verify-vault-links/
          SKILL.md
        update-vault-maps/
          SKILL.md
        detect-duplicate-or-merge-target/
          SKILL.md
```

Initial skill responsibilities:

- `connect-imported-document`: main workflow for reading the new note,
  searching related content, editing Markdown, and reporting a summary.
- `verify-vault-links`: check unresolved or malformed wikilinks after edits.
- `update-vault-maps`: maintain `30 Maps` index files.
- `detect-duplicate-or-merge-target`: identify likely duplicate notes or
  stronger merge/update targets for a future human or automated merge flow.

Later skill:

- `create-bridge-note`: create a source-grounded bridge note when multiple
  existing notes imply a missing synthesis. This should wait until simple
  linking is reliable.

## 6. Code Changes

### New Module: `connections.py`

Likely types:

```python
class VaultConnector(Protocol):
    def connect(...) -> VaultConnectionResult: ...

@dataclass(frozen=True)
class VaultConnectionResult:
    status: ConnectionStatus
    source_id: str
    primary_note_path: Path | None
    touched_paths: tuple[Path, ...]
    validation_errors: tuple[str, ...]
    agent_summary: str | None
    git_commit: VaultGitCommitResult | None

@dataclass(frozen=True)
class VaultDiffInspection:
    changed_paths: tuple[Path, ...]
    added_paths: tuple[Path, ...]
    deleted_paths: tuple[Path, ...]
    diff_stat: str

@dataclass(frozen=True)
class VaultDiffValidationResult:
    ok: bool
    staged_paths: tuple[Path, ...]
    errors: tuple[str, ...]
```

Suggested statuses:

- `not_requested`
- `skipped`
- `completed`
- `agent_failed`
- `validation_failed`
- `commit_skipped`
- `failed`

### Agent Runner Boundary

Wrap the Claude Agent SDK behind a small protocol so tests do not require live
SDK calls.

Responsibilities:

- construct `ClaudeAgentOptions`;
- pass local plugin paths;
- restrict allowed tools;
- set cwd to the vault path or app path deliberately;
- stream/capture messages;
- return the final summary and raw message metadata.

### Diff Validator

Add deterministic validation independent of the agent.

Responsibilities:

- run Git diff/status commands against the vault repo;
- normalize paths relative to the Git worktree;
- enforce allowlists and limits;
- run Obsidian or local Markdown link checks;
- return exact stageable paths.

### Git Committer

Extend the existing Git commit surface to support additional connection paths.
Avoid staging the entire worktree.

Possible interface change:

```python
def commit_ingest(
    ...,
    knowledge_note_paths: tuple[Path, ...] = (),
    connection_note_paths: tuple[Path, ...] = (),
) -> VaultGitCommitResult:
    ...
```

Alternatively add a separate `commit_connection(...)` method if we want two
commits. The preferred first implementation is one commit per ingest including
source, synthesis, and connection changes.

### Ingest Integration

Extend `ingest_file_path(...)`:

- optional `vault_connector`;
- run connector after source record and synthesis note are written;
- pass connector-touched paths to the committer;
- include `connection_result` in `LocalFileIngestResult`.

Important ordering:

1. archive/extract/enhance/synthesize;
2. write source record;
3. run connection agent;
4. validate diff;
5. commit all accepted paths.

## 7. Configuration And Commands

Environment settings:

```text
SLACK_VAULT_CONNECT_IMPORTED_DOCUMENTS=false
SLACK_VAULT_SLACK_INGEST_CONNECT=false
SLACK_VAULT_CONNECTION_MAX_TURNS=20
SLACK_VAULT_CONNECTION_MAX_TOUCHED_PATHS=12
SLACK_VAULT_CONNECTION_MAX_CHANGED_LINES=400
SLACK_VAULT_OBSIDIAN_SKILLS_PATH=90 System/agent-skills/upstream/obsidian-skills
SLACK_VAULT_CUSTOM_SKILLS_PATH=90 System/agent-skills/slack-vault
```

Skill paths should resolve relative to `SLACK_VAULT_OBSIDIAN_PATH` unless an
absolute path is supplied. This keeps the default configuration centered on the
vault repo.

CLI:

```sh
slack-vault ingest-file path/to/file.docx --synthesize --connect
slack-vault connect-note "10 Knowledge/example.md"
slack-vault validate-vault-diff --source-id source-...
```

Make:

```sh
make ingest-file FILE=path/to/file.docx SYNTHESIZE=1 CONNECT=1
make connect-note NOTE="10 Knowledge/example.md"
```

## 8. Implementation Steps

### Step A: Document And Dependency Planning

Status: complete.

Deliverables:

- this plan;
- dependency decision for `claude-agent-sdk`;
- upstream skill source decision: submodule or vendored snapshot.

Acceptance criteria:

- plan reviewed and agreed before implementation;
- no runtime behavior changes yet.

### Step B: Add Skill Sources

Status: mostly complete.

Deliverables:

- upstream Obsidian skills available locally under the Obsidian vault repo;
- Slack Vault custom skill plugin scaffold under the Obsidian vault repo;
- initial `connect-imported-document`, `verify-vault-links`, and
  `update-vault-maps` skills;
- Make target or docs for refreshing upstream skills.

Acceptance criteria:

- SDK discovery still needs a live smoke check;
- custom skills are concise and point to repo/vault-specific rules;
- upstream skills are not edited in place;
- normal connection validation rejects accidental changes to skill files.

### Step C: Build Agent Runner

Status: partially complete.

Deliverables:

- `ClaudeAgentVaultConnector` wraps the Claude Agent SDK;
- fake SDK query functions cover tests;
- result summary and errors are captured, but full raw agent-message artifact
  logging outside Git is still pending;
- bounded turn/tool settings.

Acceptance criteria:

- unit tests cover prompt construction and plugin path selection;
- missing SDK is handled by the locked dependency, but missing skill-path
  preflight should still be made clearer;
- live smoke test can ask the agent to list available skills.

### Step D: Build Diff Inspection And Validation

Status: complete for the first cut.

Deliverables:

- Git diff inspector for the vault repo;
- path allowlist validator;
- Markdown-only validator;
- no-delete validator;
- source-note/source-record preservation validator;
- wikilink validation.

Acceptance criteria:

- tests cover allowed edits, forbidden paths, deletes, renames, non-Markdown
  paths, `.obsidian` changes, source ID removal, unresolved wikilinks, and
  diff-size limits;
- validation failure leaves changes uncommitted.

### Step E: Integrate Local Ingest

Status: complete for the first cut.

Deliverables:

- `--connect` CLI option;
- `CONNECT=1` Make option;
- `VaultConnectionResult` included in ingest result;
- commit includes connector-touched paths.

Acceptance criteria:

- deterministic ingest remains unchanged by default;
- local `SYNTHESIZE=1 CONNECT=1` is implemented and expected to produce one
  vault commit with all accepted Markdown changes;
- connection skipped status is clear when no primary knowledge note exists.

### Step F: Integrate Slack Ingestion

Status: complete for the first cut.

Deliverables:

- Slack ingestion dependency builder can create the connector;
- Slack worker records connection status;
- Slack success/failure replies include connection details.

Acceptance criteria:

- Slack ingest remains unchanged while `SLACK_VAULT_SLACK_INGEST_CONNECT=false`;
- enabling connection is wired through the same clean-worktree and commit path;
- validation failures are visible in the Slack thread and operational logs.

### Step G: Add Backfill Command

Status: partially complete.

Deliverables:

- `slack-vault connect-note`;
- optional `slack-vault connect-existing --all`;
- support for connecting previously imported notes.

Acceptance criteria:

- a single existing note can be connected and committed;
- all-note backfill can run in batches with a delay or max-count limit;
- each batch starts from a clean vault worktree.

### Step H: Rollout And Tuning

Status: pending.

Deliverables:

- local smoke test on the current small vault;
- Slack smoke test with one approved document;
- prompt/skill iteration based on actual diffs;
- docs update in `README.md` and `docs/implementation-plan.md`.

Acceptance criteria:

- `make check` passes;
- vault diff from smoke test is readable and useful in Obsidian;
- no unrelated vault files are modified or committed;
- pushed vault commit can be pulled by a fresh clone.

## 9. Testing Strategy

Unit tests:

- result models and status handling;
- SDK runner prompt construction with fake runner;
- diff parsing and validation;
- allowed/forbidden path handling;
- commit staging only validated paths;
- ingest integration with fake connector;
- Slack worker integration with fake connector.

Fixture vault tests:

- small vault with two knowledge notes and map files;
- agent fake edits primary note and map file;
- validation accepts expected Markdown changes;
- validation rejects `.obsidian` or binary changes.

Live tests:

- gated by an explicit environment variable;
- use the configured Anthropic key;
- run on a disposable temp vault or explicitly approved local vault state;
- check that skills load and the agent can make a bounded connection edit.

Manual smoke tests:

- local import with `SYNTHESIZE=1 CONNECT=1`;
- Slack import with connection enabled;
- inspect Obsidian graph/backlinks after pull/open.

## 10. Failure Handling

Agent failure:

- no commit;
- source record and primary note may already be written;
- report connection status as `agent_failed`;
- leave any partial agent edits uncommitted only if they exist, with clear
  recovery instructions.

Validation failure:

- no commit;
- leave diff uncommitted for inspection;
- report exact validation errors;
- allow operator to fix manually, commit manually, or reset the vault if desired.

Commit failure:

- no push;
- report Git error;
- leave staged/unstaged state visible for operator inspection.

Push failure:

- local commit remains;
- report push failure;
- operator can retry push.

## 11. Decisions And Remaining Questions

Resolved:

1. Upstream `kepano/obsidian-skills` is tracked as a Git submodule in the
   Obsidian vault repo.
2. Local CLI `--connect` requires `--synthesize` for ingest-time connection.
   Existing synthesized notes are handled by `connect-note`.
3. The first default touched-path limit is 12 files.
4. Bridge-note creation is disabled for v1 and should wait until simple linking
   is reliable.
5. Link validation currently uses local Markdown parsing. Obsidian CLI link
   checks can be added later if local parsing proves insufficient.

Remaining questions:

1. What exact raw agent-message artifact format should we store outside Git?
2. Should batch backfill be a simple `connect-existing --all`, or should it
   require explicit note lists and max-count limits from the start?
3. After live smoke testing, should Slack connection be enabled by default or
   remain explicitly opt-in?

## 12. References

- `docs/design.md`
- `docs/implementation-plan.md`
- `src/slack_vault/ingest.py`
- `src/slack_vault/synthesis.py`
- `src/slack_vault/git_vault.py`
- `src/slack_vault/retrieval.py`
- https://github.com/kepano/obsidian-skills
- https://code.claude.com/docs/en/agent-sdk/overview
- https://code.claude.com/docs/en/agent-sdk/skills
- https://code.claude.com/docs/en/agent-sdk/plugins
