# Slack Q&A Plan

This document expands Phase 7 of the implementation plan. It covers how the
Slack bot `bizee_value_dev_bot` should answer user questions from the
Git-backed Obsidian vault.

## Goal

Expose the existing local vault Q&A path through Slack while keeping the vault
as the source of truth.

A successful Phase 7 POC lets a user:

1. Send a direct message to `bizee_value_dev_bot` with a question.
2. Receive a private answer grounded in Obsidian vault notes and source records.
3. Mention the bot in an approved channel or thread.
4. Receive a thread reply with the same grounded answer format.
5. See citations that point back to vault knowledge notes and source records.

## Recommended Approach

Use the existing local Q&A pipeline first, then wire Slack events to it.

This is not a new local-QA phase. Phase 5 already implemented local Q&A through
`slack-vault ask` / `make ask`. The first Phase 7 development slice should
extract that CLI orchestration into a reusable application service, then connect
Slack to that service through the same kind of short event handler plus worker
pattern already used for Slack ingestion.

Recommended behavior:

- Slack receives and acknowledges the event quickly.
- The app records a Q&A job in SQLite.
- The app posts a short "checking the vault" reply.
- A worker asks the configured AI provider to translate the user question into
  several concise Obsidian search queries.
- The worker runs those searches against the Obsidian vault, asks the configured
  AI provider to synthesize from retrieved vault context, and posts the final
  answer.
- If retrieval finds no useful vault context, the bot says that it could not
  find enough relevant vault context.

Do not use the AI provider as a general chat fallback in the first POC. The AI
step should synthesize and phrase answers from retrieved vault context only.
This preserves the current product boundary: Slack Vault answers from the
vault, with AI as the synthesis layer.

AI is required for Q&A. If the configured AI provider is unavailable or missing
credentials, the Q&A command or worker should fail clearly instead of attempting
to answer from a blind deterministic search.

## Current Baseline

Already implemented:

- Local retrieval loads generated knowledge notes and source records from the
  configured Obsidian vault.
- Obsidian CLI search supplies vault search hits.
- `AnthropicQuestionAnswerer` generates cited answers from retrieved context.
- `slack-vault ask "question"` and `make ask QUESTION="..."` expose the local
  Q&A path.
- Local Q&A now uses an AI-planned multi-query Obsidian search before answer
  synthesis.
- Slack Socket Mode is already used for ingestion through `make run-slack`.
- SQLite operational state already exists for Slack ingestion events and jobs.
- Slack thread replies already work for ingestion status updates.

Missing for Phase 7:

- Slack app message/mention event subscriptions for Q&A.
- Setup preflight checks for Q&A-specific scopes and bot events.
- A reusable Q&A service that is independent of CLI printing and preserves the
  AI search-planning step.
- Slack Q&A event normalization and routing.
- SQLite state for Q&A job idempotency and result tracking.
- A Q&A worker entrypoint.
- Slack-formatted answer/citation rendering.

## Slack App Requirements

`bizee_value_dev_bot` should remain the development bot for this phase.

The current ingestion app already needs:

- `chat:write`
- `files:read`
- `channels:read`
- `channels:history` for a public ingestion channel, or private-channel
  equivalents if the ingestion channel is private.

Phase 7 adds:

- `app_mentions:read` for public/channel mention handling.
- `im:history` for direct messages to the bot.
- `message.im` bot event subscription for direct-message questions.
- `app_mention` bot event subscription for mention questions.

Optional later scopes:

- `im:write` only if the bot needs to proactively open or start DMs instead of
  replying in an existing DM channel.
- `mpim:history` only if multi-person DM Q&A becomes in scope.

Manual Slack app setup to verify:

1. The bot user exists and is installed in the development workspace.
2. Users can send messages to the app. If the Slack app UI has the App Home
   messages tab disabled, enable app messages before testing DM Q&A.
3. Socket Mode remains enabled.
4. Event subscriptions include `message.im` and `app_mention`.
5. OAuth scopes include `app_mentions:read`, `im:history`, and `chat:write`.
6. The bot is invited to any channel where mention-based Q&A should work.

## User Experience

### Direct Message

User sends:

```text
What does the HubSpot current state document say about current risks?
```

Bot replies in the same DM:

```text
Checking the vault for relevant notes...
```

Then the bot posts the final answer:

```text
The current-state notes identify duplicate process ownership and inconsistent
handoff tracking as the main risks [1].

Evidence:
[1] HubSpot Current State - 10 Knowledge/hubspot-current-state.md
    Source: source-2026-06-13-b7b566e4c0e9
```

### Channel Mention

User posts in an approved channel:

```text
@bizee_value_dev_bot what does the vault say about onboarding gaps?
```

Bot replies in thread, not as a broadcast, unless the implementation later adds
an explicit broadcast option.

### No Evidence

If retrieval returns no useful context:

```text
I could not find enough relevant vault context to answer this question.
```

The bot should not fabricate a general AI answer.

## Event Routing Rules

Handle:

- `message` events where `channel_type == "im"`.
- `app_mention` events in channels where the bot is present.

Ignore:

- bot-authored messages;
- Slack retries or duplicate event IDs already recorded;
- file-upload messages already handled by Slack ingestion;
- messages with no meaningful question text after stripping bot mentions;
- events from external shared channels unless explicitly allowed later;
- broad channel messages that do not mention the bot.

For mentions, strip the bot user mention token from the question before
retrieval.

## Operational State

Extend SQLite state rather than creating a separate store.

Proposed tables:

### `slack_qa_events`

- `event_id`
- `event_type`
- `enterprise_id`
- `team_id`
- `context_team_id`
- `channel_id`
- `channel_type`
- `user_id`
- `event_ts`
- `message_ts`
- `thread_ts`
- `raw_payload_json`
- `received_at`
- `duplicate_of_event_id`

### `qa_jobs`

- `job_id`
- `status`: `queued`, `running`, `succeeded`, `failed`, `ignored`
- `slack_event_id`
- `dedupe_key`
- `enterprise_id`
- `team_id`
- `context_team_id`
- `channel_id`
- `channel_type`
- `user_id`
- `message_ts`
- `thread_ts`
- `question_text`
- `search_query`
- `answer_text`
- `citations_json`
- `slack_initial_message_ts`
- `slack_result_message_ts`
- `error_stage`
- `error_message`
- `created_at`, `started_at`, `finished_at`

Initial idempotency:

- `slack_qa_events.event_id` handles Slack retries.
- `qa_jobs.dedupe_key` can use `(team_id, channel_id, message_ts)` so one Slack
  message creates one Q&A job.

## Code Plan

### Phase 7.0: Planning And App Setup

Deliverables:

- This plan document.
- Updated setup documentation for Q&A scopes and events.
- `check-slack-setup` extended to verify Q&A requirements when Q&A is enabled.

Acceptance criteria:

- A developer can see exactly which Slack app settings are required before code
  testing starts.
- The setup check reports missing Q&A scopes/events clearly.

### Phase 7.1: Reusable Local Q&A Service

Deliverables:

- Move CLI Q&A orchestration into a reusable service, for example
  `answer_question_from_settings(settings, question, limit=5)`.
- Keep `slack-vault ask` behavior unchanged.
- Add focused tests for no-evidence, mocked AI answer, and provider failure
  behavior through the reusable service.

Acceptance criteria:

- Local Q&A still works through `make ask`.
- Slack code can call one service method without duplicating CLI logic.

### Phase 7.2: Q&A Job State And Worker

Deliverables:

- Add SQLite Q&A event/job persistence.
- Add `slack-vault slack-qa-worker` and `make slack-qa-worker`.
- Worker loads one queued question, runs the reusable Q&A service, and records
  success or failure.
- Worker posts the final Slack answer through `chat.postMessage`.

Acceptance criteria:

- Unit tests can enqueue a synthetic Q&A job and process it with mocked Slack
  and mocked AI.
- A failed AI or Obsidian search call is recorded and reported in Slack without
  losing the job state.

### Phase 7.3: Direct Message Q&A

Deliverables:

- Add Slack `message.im` handling to the Bolt app.
- Normalize direct-message events into Q&A jobs.
- Post an immediate "checking the vault" reply.
- Spawn `make slack-qa-worker ONCE=1` for each newly queued Q&A job.

Acceptance criteria:

- A direct message to `bizee_value_dev_bot` triggers one Q&A job.
- Duplicate Slack retries do not create duplicate jobs.
- Bot-authored DM messages are ignored.
- The final answer appears in the same DM.

### Phase 7.4: Mention And Thread Q&A

Deliverables:

- Add Slack `app_mention` handling to the Bolt app.
- Strip bot mention syntax from the question text.
- Reply in the original thread, using the parent thread timestamp when present.
- Keep broad channel messages without mentions out of scope.

Acceptance criteria:

- A channel mention creates one Q&A job.
- The bot replies in thread.
- Duplicate mention events do not create duplicate answers.
- The bot does not answer ordinary channel chatter.

### Phase 7.5: Live Smoke Tests And Docs

Deliverables:

- Update README and implementation status with Q&A run commands.
- Add a live manual smoke-test checklist.
- Optionally add a gated live Slack Q&A test that verifies event access and
  `chat.postMessage` without running a full AI answer.

Acceptance criteria:

- `make check` passes.
- `make check-slack-setup` validates ingestion and Q&A requirements.
- Local smoke test: `make ask QUESTION="..."` still returns a cited answer.
- Slack DM smoke test: DM the bot and receive a cited vault-grounded answer.
- Slack mention smoke test: mention the bot in an approved channel and receive a
  thread reply.

## Slack Answer Formatting

Prefer simple Slack mrkdwn over complex Block Kit for the first POC.

Rules:

- Include a concise direct answer first.
- Include `Evidence:` with numbered citations.
- Show vault-relative paths, not local absolute paths.
- Include source IDs when available.
- Keep the message under Slack practical length limits; truncate citation
  details before truncating the direct answer.
- Avoid posting archive URIs, local filesystem paths, private Slack URLs, or
  secrets.

## Questions To Resolve Before Coding

These are not blockers for writing the first service and tests, but they affect
the live Slack smoke test:

1. Is `bizee_value_dev_bot` the same Slack app already used for ingestion, or a
   separate bot that should share the same code but use separate credentials?
2. Should channel mention Q&A be allowed in any channel where the bot is
   invited, or only in an allowlisted POC Q&A channel?
3. Should DM Q&A be available to any workspace user who can message the bot, or
   should Phase 7 add a user allowlist?
4. Should answers cite only committed vault content, or is it acceptable to read
   uncommitted local vault changes during development? The current local Q&A
   reads the configured vault working tree.

## Out Of Scope For First Phase 7 Slice

- General AI answers that are not grounded in vault retrieval.
- Vector retrieval.
- Permission-aware retrieval by Slack user.
- Multi-workspace routing beyond metadata capture.
- Slack modals, shortcuts, slash commands, or interactive buttons.
- Long-term conversation memory.
- Proactive outbound DMs.
- Production deployment or HTTP Events API ingress.

## References

- Slack `app_mention` event: <https://docs.slack.dev/reference/events/app_mention/>
- Slack `message.im` event: <https://docs.slack.dev/reference/events/message.im/>
- Slack `chat.postMessage` method: <https://docs.slack.dev/reference/methods/chat.postMessage/>
