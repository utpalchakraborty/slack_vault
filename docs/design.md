# Slack Vault Design

## 1. Purpose

Slack Vault is an organization knowledge repository built around an Obsidian vault.
The system ingests source documents shared by users in Slack, extracts and
synthesizes their knowledge into Markdown notes, and makes that knowledge
queryable from Slack with citations.

The vault is not intended to be a raw document dump. Original files are archived
outside Git, while the Obsidian vault stores living Markdown knowledge notes,
source metadata, indexes, taxonomy guidance, and provenance links.

## 2. Goals

- Provide a shared organization knowledgebase that can be browsed in Obsidian.
- Let users ingest documents from Slack with minimal friction.
- Let users query the knowledgebase from Slack.
- Store synthesized knowledge as Markdown so it remains durable, portable, and
  human-readable.
- Allow anyone with repository access to clone the vault and open it directly in
  Obsidian without running the Slack bot, ingestion workers, or retrieval
  services.
- Preserve citations back to source evidence.
- Keep original source documents outside the Git-backed vault.
- Use AI for extraction, classification, synthesis, organization, and answering.
- Allow organization-specific guidance through skills, prompts, and taxonomy
  files.
- Support local prototyping first, with a path to shared production deployment.

## 3. Non-Goals For The Initial Version

- Permission-aware retrieval across users, teams, or channels.
- Multi-workspace Slack Enterprise Grid support.
- Human approval before every vault update.
- Perfect extraction for every possible document format.
- Full Obsidian Sync automation.
- Replacing Slack or Obsidian as user-facing tools.

## 4. Assumptions

- The first version targets one Slack workspace.
- All users can access the shared knowledgebase.
- Source document contents may be sent to OpenAI or Anthropic APIs.
- The system should process everything submitted to the ingestion channel during
  the proof of concept.
- The design should allow later preprocessing to filter low-value or noisy
  content.
- Source documents are immutable evidence artifacts.
- Knowledge notes are living semantic objects that can be updated by later
  evidence.
- The Git repository should be a complete, usable Obsidian vault for human
  browsing, search, backlinks, graph navigation, properties, and saved views.
- Git stores Markdown vault content, not large original files.
- Original files are stored through an archive provider.
- Initial archive provider implementations are local filesystem and Google Cloud.

## 5. Conceptual Model

The system separates source evidence from synthesized knowledge.

### 5.1 Source Documents

A source document is an immutable artifact received by the system. Examples:

- PDF
- Markdown file
- Excel workbook
- Image
- Word document
- Plain text file
- Slack message comment associated with an uploaded file

Each source document receives a stable source record. The source record preserves
metadata such as origin, uploader, Slack message link, archive URI, content hash,
MIME type, extracted text status, and ingestion timestamp.

### 5.2 Knowledge Notes

A knowledge note is a living Markdown document in the Obsidian vault. It
represents the current synthesized understanding of a topic, project, policy,
plan, process, meeting series, customer, product, or other organizational
knowledge object.

For example, multiple uploaded documents may all update the same note:

- `Marketing Plan for Widget A`
- `Widget A GTM Strategy`
- `Q3 Widget A Campaign Brief`

The system should not blindly create one knowledge note per source document.
Instead, it should decide whether new evidence creates a new knowledge object,
updates an existing one, adds citations to an existing claim, or introduces a
conflict that must be represented explicitly.

### 5.3 Provenance

Every important synthesized claim should be traceable back to evidence. A
knowledge note may cite many source records. A source record may support many
knowledge notes.

The original source file may eventually move or disappear from the archive, so
the vault should preserve enough metadata and extracted evidence to explain why a
claim exists.

## 6. High-Level Architecture

```text
Slack
  |
  v
Slack App / Bot
  |
  +--> Ingestion Pipeline
  |      |
  |      +--> Archive Provider
  |      +--> Source Registry
  |      +--> Document Extractors
  |      +--> AI Classification
  |      +--> AI Knowledge Synthesis
  |      +--> Vault Writer
  |      +--> Git Committer
  |
  +--> Q&A Pipeline
         |
         +--> Vault Search
         +--> Retrieval Index
         +--> Source Registry
         +--> AI Answer Generator
         +--> Slack Response With Citations
```

## 7. Main Components

### 7.1 Slack App / Bot

The Slack app is the primary interface for ingestion and Q&A.

Initial responsibilities:

- Listen to a configured ingestion channel.
- Download uploaded files from Slack.
- Capture uploader, channel, message timestamp, thread timestamp, and comments.
- Acknowledge ingestion requests.
- Accept direct messages and mentions for Q&A.
- Reply privately or publicly depending on Slack context.

Answer behavior:

- In a direct message, answer privately.
- In a public channel mention or thread, answer in that conversation.
- For ingestion events, reply in the relevant Slack thread with status and links
  to created or updated vault notes when available.

### 7.2 Archive Provider

The archive provider stores original source files outside the Git-backed vault.
The system should treat archive storage as an abstraction so local development
and production deployments can use different backends.

Conceptual interface:

```text
ArchiveProvider
  save_source(file, metadata) -> ArchivedSourceRef
  get_source(ref) -> file_stream
  get_source_metadata(ref) -> metadata
  get_access_url(ref) -> url
  exists(ref) -> bool
  list_sources(filters) -> source_refs
```

`ArchivedSourceRef` should include:

```yaml
archive_provider: local | gcs
archive_id: stable archive-specific id
uri: file path or gs:// URI
content_hash: sha256 hash
original_filename: original uploaded filename
mime_type: source MIME type
size_bytes: original size
created_at: archive timestamp
slack_workspace_id: Slack workspace id
slack_channel_id: Slack channel id
slack_message_ts: Slack message timestamp
slack_file_id: Slack file id
uploaded_by: Slack user id
```

#### Local Filesystem Provider

Used for development and local prototyping.

Example layout:

```text
archive/
  sources/
    2026/
      06/
        <content-hash>/
          original
          metadata.json
```

#### Google Cloud Provider

Used for shared production deployment. The first concrete target should be
Google Cloud Storage.

Example layout:

```text
gs://org-knowledge-archive/
  sources/
    2026/
      06/
        <content-hash>/
          original
          metadata.json
```

The design should leave room for future Google Drive or Shared Drive integration
if the organization wants source documents to remain in a user-facing document
system instead of an object store.

### 7.3 Source Registry

The source registry records immutable evidence metadata inside the vault as
Markdown. This makes source provenance browseable in Obsidian and versioned in
Git.

Example vault path:

```text
20 Sources/sources/source-2026-06-13-abc123.md
```

Example source note:

```markdown
---
title: Widget A Marketing Plan v2.pdf
type: source_record
source_id: source-2026-06-13-abc123
archive_provider: gcs
archive_uri: gs://org-knowledge-archive/sources/2026/06/abc123/original
content_hash: abc123
mime_type: application/pdf
uploaded_by: U123456
slack_workspace_id: T123456
slack_channel_id: C123456
slack_message_ts: "1781366400.000100"
ingested_at: 2026-06-13T12:00:00Z
extraction_status: complete
---

# Widget A Marketing Plan v2.pdf

## Origin

- Slack channel: C123456
- Slack message timestamp: 1781366400.000100
- Uploaded by: U123456

## Extracted Evidence

...
```

### 7.4 Document Extractors

Document extractors convert heterogeneous source files into normalized text and
structured evidence.

Initial supported formats:

- Markdown
- Plain text
- PDF
- Word documents

Next supported formats:

- Excel workbooks
- Images with OCR and vision extraction
- Presentation files, if needed

Extraction output should preserve useful location references where possible:

- PDF page numbers
- Word headings
- Spreadsheet sheet names and cell ranges
- Image filename and OCR regions
- Slack message permalink

### 7.5 AI Classification And Synthesis

The AI layer decides what knowledge should be created or updated.

Responsibilities:

- Classify document type and topic.
- Propose tags and folder placement.
- Detect whether a source updates existing notes.
- Detect duplicate or near-duplicate content.
- Detect conflicting or superseding information.
- Generate or update Markdown knowledge notes.
- Add citations to source records and extracted evidence.
- Respect organization-specific skills and taxonomy guidance.

The synthesis step should favor explicit uncertainty. If the system is not sure
whether a document updates an existing note or creates a new one, it should
record that ambiguity in metadata or create a clearly marked candidate note.

### 7.6 Vault Writer

The vault writer applies AI-generated changes to the Obsidian vault.

Responsibilities:

- Create source record notes.
- Create or update knowledge notes.
- Maintain frontmatter.
- Maintain backlinks and source citations.
- Avoid destructive rewrites of human edits when possible.
- Format Markdown consistently.
- Stage changes for Git commit.

The vault remains human-editable, but human editing is not required for normal
operation.

### 7.7 Git Committer

Every successful ingestion should produce a Git commit containing the resulting
vault changes.

Commit messages should be structured enough to audit:

```text
Ingest source: Widget A Marketing Plan v2.pdf

Source: source-2026-06-13-abc123
Slack: C123456 / 1781366400.000100
Updated notes:
- 10 Knowledge/Products/Widget A/Marketing Plan.md
```

Git provides history, rollback, and reviewability. It is not the storage layer
for large binary source files.

### 7.8 Retrieval And Search

The Q&A system should use multiple retrieval methods:

- Obsidian-compatible Markdown text search.
- Frontmatter metadata search.
- Source registry search.
- Vector search over knowledge notes and extracted evidence.

The initial implementation can start with filesystem search plus a simple local
or hosted vector index. The design should preserve Markdown as the canonical
knowledge format so the system does not become dependent on an opaque vector
database.

### 7.9 Obsidian-Native Usage

The Git repository should be directly useful as an Obsidian vault. A user should
be able to clone the repository, open the vault folder in Obsidian, and browse
the knowledgebase without installing or running the Slack integration.

Design implications:

- Markdown files are the canonical human-readable knowledge surface.
- Frontmatter properties should use stable, consistent fields so Obsidian can
  search, filter, and display them.
- Wikilinks should connect knowledge notes, source records, maps, and related
  topics.
- Backlinks should provide useful provenance and navigation.
- Tags should be broad enough for discovery but not so granular that they become
  noise.
- Saved Obsidian views should expose important operational slices of the vault,
  such as recent ingestions, all source records, active knowledge notes, and
  unresolved conflicts.
- The vault should not require community plugins for basic readability or
  navigation.
- Optional Obsidian features and community plugins may enhance the experience,
  but the core vault should remain plain Markdown plus standard Obsidian
  conventions.

The Slack Q&A backend can use its own retrieval index, but that index should be
derived from the vault rather than becoming the canonical knowledge store.

### 7.10 Slack Q&A

The Q&A pipeline answers user questions from Slack.

Workflow:

1. User asks the bot a question in DM, mention, or thread.
2. The bot determines response visibility from Slack context.
3. The retrieval system finds relevant vault notes and source records.
4. The answer generator produces a concise answer grounded in retrieved content.
5. The bot replies with citations to vault notes and source evidence.

Answer format should include:

- Direct answer.
- Important caveats or uncertainty.
- Citations to knowledge notes.
- Citations to source records, including page, section, sheet, or other location
  references when available.

Example:

```text
Widget A's current marketing plan focuses on enterprise healthcare buyers and
partner-led campaigns for Q3.

Evidence:
1. Marketing Plan for Widget A, section "Current Strategy"
2. Source: Widget A Marketing Plan v2.pdf, page 4
3. Source: Q3 GTM Notes, section "Partner Motion"
```

## 8. Proposed Vault Structure

```text
vault/
  00 Inbox/
    pending-ingestions.md

  10 Knowledge/
    Customers/
    Meetings/
    Policies/
    Processes/
    Products/
    Projects/
    Teams/

  20 Sources/
    source-index.md
    sources/
      source-2026-06-13-abc123.md

  30 Maps/
    customer-index.md
    product-index.md
    project-index.md
    team-index.md
    topic-index.md

  40 Views/
    active-knowledge-notes.base
    recent-ingestions.base
    source-records.base
    unresolved-conflicts.base

  90 System/
    ingestion-guidelines.md
    prompt-guidelines.md
    taxonomy-guidelines.md
```

This is a starter taxonomy. The system should allow the AI to extend it over
time, guided by files under `90 System/`.

## 9. Knowledge Note Format

Example:

```markdown
---
title: Marketing Plan for Widget A
type: knowledge_note
status: active
topics:
  - marketing
  - widget-a
entities:
  products:
    - Widget A
sources:
  - source-2026-06-13-abc123
  - source-2026-06-14-def456
last_synthesized_at: 2026-06-14T10:22:00Z
confidence: medium
---

# Marketing Plan for Widget A

## Summary

...

## Current Strategy

...

## Changes Over Time

...

## Open Questions

...

## Source Evidence

- [[source-2026-06-13-abc123]] page 4
- [[source-2026-06-14-def456]] section "Q3 Campaign"
```

## 10. Skills, Prompts, And MCP Servers

The system should support organization-specific behavior through skills and MCP
servers.

### 10.1 Skills

Skills can encode extraction and synthesis guidance for specific content types.

Examples:

```text
skills/
  customer-research/
  engineering-rfcs/
  marketing-docs/
  meeting-notes/
  sales-enablement/
```

A skill may define:

- How to recognize a document type.
- Which sections to extract.
- How to structure the resulting Markdown.
- Which tags and folders are preferred.
- How to identify important claims.
- How citations should be represented.

### 10.2 MCP Servers

MCP servers can expose the vault and archive to AI agents.

Possible `vault-server` tools:

```text
search_notes
read_note
write_note
list_recent_changes
find_related_notes
get_note_backlinks
```

Possible `archive-server` tools:

```text
get_source_metadata
fetch_source_text
get_source_url
list_sources
```

MCP is not required for the very first proof of concept, but the design should
keep the boundary clean enough to add MCP servers later.

## 11. Deployment Model

### 11.1 Local Prototype

Runs on a developer machine.

Likely components:

- Local Obsidian vault directory.
- Local archive directory.
- Slack app using a development workspace or test channel.
- Local worker process.
- Local SQLite database for operational state.
- OpenAI or Anthropic API access.
- Git repository for the vault.

### 11.2 Shared Production Deployment

Runs in shared infrastructure accessible to the organization.

Likely components:

- Slack app installed in the organization workspace.
- Worker service running in a cloud environment.
- Google Cloud Storage archive bucket.
- Git-hosted vault repository.
- Managed database for ingestion state, source records, jobs, and indexes.
- Vector index for retrieval.
- Secrets manager for Slack and AI provider credentials.
- Monitoring and alerting for ingestion failures.

## 12. Operational State

The vault is canonical for human-readable knowledge, but the system still needs
operational state that should not be stored only in Markdown.

Likely database tables or collections:

- `ingestion_jobs`
- `source_artifacts`
- `extraction_results`
- `knowledge_note_updates`
- `slack_events`
- `retrieval_index_records`
- `failed_jobs`

This state supports retries, idempotency, observability, and async processing.

## 13. Ingestion Workflow

Initial Slack ingestion flow:

1. User posts a document in the ingestion channel with optional comments.
2. Slack sends a file event or message event to the Slack app.
3. The app creates an ingestion job.
4. The worker downloads the file from Slack.
5. The archive provider stores the original file.
6. The system creates or updates a source record.
7. The document extractor produces normalized text and structured evidence.
8. AI classification determines topics, document type, and likely target notes.
9. AI synthesis creates or updates knowledge notes.
10. The vault writer applies Markdown changes.
11. The Git committer commits the vault changes.
12. The Slack bot replies with the ingestion result.

The POC should process all submitted documents. Later versions can add
preprocessing to skip or quarantine low-value content.

## 14. Q&A Workflow

Initial Slack Q&A flow:

1. User asks the bot a question.
2. The bot determines whether the answer should be public or private.
3. The retrieval system searches knowledge notes, source records, and indexes.
4. The answer generator drafts an answer from retrieved context only.
5. The answer generator attaches citations.
6. The bot posts the answer in the appropriate Slack context.

The answer generator should avoid unsupported claims. If the vault does not
contain enough evidence, the bot should say so and optionally suggest related
notes or sources.

## 15. Scaling Considerations

The system should eventually support hundreds of users and gigabytes of source
data. That implies:

- Async ingestion jobs.
- Idempotent event processing.
- Content hashing to detect duplicate uploads.
- Archive storage outside Git.
- Incremental indexing.
- Job retries and dead-letter handling.
- Observability for failed extraction and failed synthesis.
- Rate-limit handling for Slack and AI providers.
- Token-aware document chunking.
- Periodic vault maintenance and index rebuilding.

The first version does not need to solve all scaling problems, but its
interfaces should avoid blocking that path.

## 16. Proof Of Concept Scope

The first useful proof of concept should prove the full round trip:

- Local filesystem archive provider.
- Local Git-backed Obsidian vault.
- Cloneable vault that opens directly in Obsidian.
- One Slack ingestion channel.
- File ingestion for Markdown, plain text, PDF, and Word documents.
- AI-generated source records.
- AI-created or AI-updated knowledge notes.
- Simple vault search and retrieval.
- Slack Q&A with citations.
- One Git commit per successful ingestion.

Explicitly deferred:

- Permission-aware retrieval.
- Multi-workspace Slack support.
- Advanced spreadsheet extraction.
- Advanced image/OCR extraction.
- Obsidian Sync automation.
- Production monitoring.
- Human review workflows.

## 17. Open Questions

- Should production source archives use only Google Cloud Storage, or should
  Google Drive / Shared Drive also be a first-class archive provider?
- Which Git host should store the vault repository?
- Should the Q&A bot search only committed vault content, or also recently
  processed but uncommitted artifacts?
- What starter taxonomy should the organization provide before AI-generated
  taxonomy expansion begins?
- What database should be used for local and production operational state?
- Which vector index should be used for the first implementation?
- How should conflicting claims be represented in knowledge notes?
- How should the system detect and preserve human edits during automated note
  updates?
