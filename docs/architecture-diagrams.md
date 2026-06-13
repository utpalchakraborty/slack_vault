# Architecture Diagrams

This document contains Mermaid diagrams for the Slack Vault architecture.

## Component Architecture

```mermaid
flowchart TB
    subgraph Slack["Slack Workspace"]
        IngestionChannel["Ingestion Channel"]
        UserDM["User DM"]
        PublicThread["Public Channel / Thread"]
        SlackFiles["Slack Files"]
    end

    subgraph App["Slack App / Bot"]
        EventHandler["Event Handler"]
        IngestionRouter["Ingestion Router"]
        QuestionRouter["Q&A Router"]
        SlackResponder["Slack Responder"]
    end

    subgraph Workers["Backend Workers"]
        JobQueue["Job Queue"]
        IngestionWorker["Ingestion Worker"]
        ExtractionWorker["Document Extraction"]
        Classification["AI Classification"]
        Synthesis["AI Knowledge Synthesis"]
        VaultWriter["Vault Writer"]
        GitCommitter["Git Committer"]
        QAWorker["Q&A Worker"]
        Retrieval["Retrieval Orchestrator"]
        AnswerGenerator["AI Answer Generator"]
    end

    subgraph Archive["Archive Providers"]
        ArchiveInterface["ArchiveProvider Interface"]
        LocalArchive["Local Filesystem Archive"]
        GCSArchive["Google Cloud Storage Archive"]
    end

    subgraph VaultRepo["Git-Backed Obsidian Vault"]
        KnowledgeNotes["10 Knowledge/*.md"]
        SourceRecords["20 Sources/*.md"]
        Maps["30 Maps/*.md"]
        Views["40 Views/*.base"]
        SystemGuidance["90 System/*.md"]
    end

    subgraph Indexes["Derived Indexes"]
        TextSearch["Filesystem / Markdown Search"]
        MetadataSearch["Frontmatter Metadata Index"]
        VectorIndex["Vector Index"]
        OperationalDB["Operational Database"]
    end

    subgraph AI["AI Providers"]
        OpenAI["OpenAI"]
        Claude["Claude"]
        LocalModels["Local Models"]
    end

    subgraph Obsidian["Human Obsidian Usage"]
        CloneRepo["Git Clone Vault"]
        ObsidianApp["Open In Obsidian"]
        NativeSearch["Obsidian Search / Properties / Backlinks"]
    end

    IngestionChannel --> EventHandler
    UserDM --> EventHandler
    PublicThread --> EventHandler
    EventHandler --> IngestionRouter
    EventHandler --> QuestionRouter

    IngestionRouter --> JobQueue
    JobQueue --> IngestionWorker
    IngestionWorker --> SlackFiles
    IngestionWorker --> ArchiveInterface
    ArchiveInterface --> LocalArchive
    ArchiveInterface --> GCSArchive
    IngestionWorker --> SourceRecords
    IngestionWorker --> ExtractionWorker
    ExtractionWorker --> Classification
    Classification --> Synthesis
    Synthesis --> VaultWriter
    VaultWriter --> KnowledgeNotes
    VaultWriter --> SourceRecords
    VaultWriter --> Maps
    VaultWriter --> Views
    VaultWriter --> GitCommitter
    GitCommitter --> VaultRepo
    GitCommitter --> SlackResponder

    QuestionRouter --> QAWorker
    QAWorker --> Retrieval
    Retrieval --> TextSearch
    Retrieval --> MetadataSearch
    Retrieval --> VectorIndex
    Retrieval --> KnowledgeNotes
    Retrieval --> SourceRecords
    Retrieval --> AnswerGenerator
    AnswerGenerator --> SlackResponder

    Classification --> OpenAI
    Classification --> Claude
    Classification --> LocalModels
    Synthesis --> OpenAI
    Synthesis --> Claude
    Synthesis --> LocalModels
    AnswerGenerator --> OpenAI
    AnswerGenerator --> Claude
    AnswerGenerator --> LocalModels

    IngestionWorker --> OperationalDB
    ExtractionWorker --> OperationalDB
    VaultWriter --> OperationalDB
    Retrieval --> OperationalDB

    SlackResponder --> UserDM
    SlackResponder --> PublicThread
    SlackResponder --> IngestionChannel

    VaultRepo --> TextSearch
    VaultRepo --> MetadataSearch
    VaultRepo --> VectorIndex

    VaultRepo --> CloneRepo
    CloneRepo --> ObsidianApp
    ObsidianApp --> NativeSearch
```

## Ingestion Flow

```mermaid
sequenceDiagram
    autonumber
    participant User as Slack User
    participant Slack as Slack
    participant Bot as Slack App / Bot
    participant Queue as Job Queue
    participant Worker as Ingestion Worker
    participant Archive as ArchiveProvider
    participant Extractor as Document Extractor
    participant AI as AI Classifier / Synthesizer
    participant Vault as Obsidian Vault
    participant Git as Git Repository

    User->>Slack: Upload source document with optional comment
    Slack->>Bot: File/message event
    Bot->>Queue: Create ingestion job
    Bot-->>User: Acknowledge ingestion request
    Queue->>Worker: Start ingestion job
    Worker->>Slack: Download file
    Worker->>Archive: Store immutable source artifact
    Archive-->>Worker: ArchivedSourceRef
    Worker->>Vault: Create source record Markdown
    Worker->>Extractor: Extract normalized text and evidence
    Extractor-->>Worker: Extracted content with locations
    Worker->>AI: Classify, match existing notes, synthesize updates
    AI-->>Worker: Proposed knowledge note changes with citations
    Worker->>Vault: Create or update Markdown notes
    Worker->>Git: Commit vault changes
    Git-->>Worker: Commit id
    Worker->>Bot: Ingestion result
    Bot-->>User: Reply with created/updated notes and status
```

## Slack Q&A Flow

```mermaid
sequenceDiagram
    autonumber
    participant User as Slack User
    participant Slack as Slack
    participant Bot as Slack App / Bot
    participant QA as Q&A Worker
    participant Vault as Obsidian Vault
    participant Index as Derived Retrieval Indexes
    participant AI as AI Answer Generator

    User->>Slack: Ask question in DM, mention, or thread
    Slack->>Bot: Message event
    Bot->>QA: Route question with Slack context
    QA->>Vault: Search Markdown notes and source records
    QA->>Index: Search metadata and vector indexes
    Index-->>QA: Candidate notes and evidence
    Vault-->>QA: Markdown context and citations
    QA->>AI: Generate grounded answer from retrieved context
    AI-->>QA: Answer with citations and caveats
    QA->>Bot: Response payload
    Bot-->>Slack: Post privately or publicly based on context
    Slack-->>User: Answer with vault/source citations
```

## Obsidian Clone-And-Use Flow

```mermaid
flowchart LR
    GitHost["Git-Hosted Vault Repository"]
    LocalClone["User Clones Repository"]
    Obsidian["Open Folder In Obsidian"]
    Browse["Browse Markdown Notes"]
    Search["Use Obsidian Search"]
    Properties["Use Properties / Bases"]
    Backlinks["Use Wikilinks / Backlinks / Graph"]

    GitHost --> LocalClone
    LocalClone --> Obsidian
    Obsidian --> Browse
    Obsidian --> Search
    Obsidian --> Properties
    Obsidian --> Backlinks
```
