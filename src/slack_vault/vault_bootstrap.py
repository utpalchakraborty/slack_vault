"""Create the starter Obsidian vault structure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VAULT_DIRECTORIES = (
    "00 Inbox",
    "10 Knowledge/Customers",
    "10 Knowledge/Meetings",
    "10 Knowledge/Policies",
    "10 Knowledge/Processes",
    "10 Knowledge/Products",
    "10 Knowledge/Projects",
    "10 Knowledge/Teams",
    "20 Sources/sources",
    "30 Maps",
    "40 Views",
    "90 System",
)

STARTER_FILES = {
    "10 Knowledge/Customers/.gitkeep": "",
    "10 Knowledge/Meetings/.gitkeep": "",
    "10 Knowledge/Policies/.gitkeep": "",
    "10 Knowledge/Processes/.gitkeep": "",
    "10 Knowledge/Products/.gitkeep": "",
    "10 Knowledge/Projects/.gitkeep": "",
    "10 Knowledge/Teams/.gitkeep": "",
    "00 Inbox/pending-ingestions.md": """---
title: Pending Ingestions
type: inbox
---

# Pending Ingestions

This note is reserved for sources that require manual follow-up or review.
""",
    "20 Sources/source-index.md": """---
title: Source Index
type: source_index
---

# Source Index

Source records created by the ingestion pipeline are listed here.
""",
    "20 Sources/sources/.gitkeep": "",
    "30 Maps/customer-index.md": """---
title: Customer Index
type: map
---

# Customer Index
""",
    "30 Maps/product-index.md": """---
title: Product Index
type: map
---

# Product Index
""",
    "30 Maps/project-index.md": """---
title: Project Index
type: map
---

# Project Index
""",
    "30 Maps/team-index.md": """---
title: Team Index
type: map
---

# Team Index
""",
    "30 Maps/topic-index.md": """---
title: Topic Index
type: map
---

# Topic Index
""",
    "40 Views/README.md": """---
title: Views
type: views_index
---

# Views

Saved Obsidian views and Bases belong here as the vault matures.
""",
    "90 System/ingestion-guidelines.md": """---
title: Ingestion Guidelines
type: system_guidance
---

# Ingestion Guidelines

- Preserve original source files in the configured archive, not in this vault.
- Create one source record for each immutable source artifact.
- Prefer updating an existing knowledge note when new evidence clearly belongs
  to the same knowledge object.
- Record ambiguity instead of guessing when evidence does not clearly match an
  existing note.
- Every important synthesized claim should cite source evidence.
""",
    "90 System/taxonomy-guidelines.md": """---
title: Taxonomy Guidelines
type: system_guidance
---

# Taxonomy Guidelines

- Use broad, stable folders for primary navigation.
- Prefer Obsidian wikilinks for named entities and related knowledge notes.
- Keep tags useful for discovery; avoid one-off tags that duplicate note names.
- Extend the starter taxonomy only when a new category will be reused.
""",
    "90 System/prompt-guidelines.md": """---
title: Prompt Guidelines
type: system_guidance
---

# Prompt Guidelines

- Ground generated notes in extracted evidence.
- Include citations to source records and evidence locations.
- State uncertainty and conflicts explicitly.
- Preserve human-authored edits when updating existing notes.
- Keep Markdown readable in Obsidian without custom plugins.
""",
    ".gitignore": """.obsidian/
.trash/
""",
}


@dataclass(frozen=True)
class VaultBootstrapResult:
    """Summary of a vault bootstrap run."""

    vault_path: Path
    created_paths: tuple[Path, ...]


def bootstrap_vault(
    vault_path: Path, *, overwrite: bool = False
) -> VaultBootstrapResult:
    """Create starter directories and files for an Obsidian vault."""

    created_paths: list[Path] = []
    vault_path.mkdir(parents=True, exist_ok=True)

    for directory in VAULT_DIRECTORIES:
        target = vault_path / directory
        target.mkdir(parents=True, exist_ok=True)
        created_paths.append(target)

    for relative_path, content in STARTER_FILES.items():
        target = vault_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            continue
        target.write_text(content, encoding="utf-8")
        created_paths.append(target)

    return VaultBootstrapResult(
        vault_path=vault_path,
        created_paths=tuple(created_paths),
    )
