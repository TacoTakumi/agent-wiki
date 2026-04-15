# Agent Wiki

A personal knowledge base for AI agents, inspired by [Karpathy's LLM wiki concept](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). A Python CLI tool manages a plain-markdown vault that any agent can read and write. Claude Code skills provide tight integration, but the vault is just files — open it in Obsidian, grep it, script against it.

## Vision

Agents accumulate knowledge across conversations but lose it when the session ends. Agent Wiki is a persistent, compounding knowledge store that agents maintain alongside you. The human curates sources and asks questions; the agent handles the bookkeeping — filing research, maintaining cross-references, surfacing relevant knowledge before reaching for the web.

The wiki is designed to be agent-first but human-readable. It's plain markdown files you can browse in Obsidian, grep from a terminal, or script against with Python. No databases, no servers, no lock-in.

## Installation

Requires Python 3.10+.

```bash
cd agent-wiki
uv venv && source .venv/bin/activate
uv pip install -e .
```

This installs two commands: `awiki` and `aw` (short alias). They are identical.

### Optional

- **ripgrep** (`rg`) — search uses ripgrep when available, falls back to Python regex.
- **pytest** — `uv pip install pytest` to run tests.

## Quick Start

```bash
# Create a vault
awiki init ~/vaults/agent-wiki

# Ingest a file
awiki ingest my-notes.md --topic research --tags python,testing

# Search
awiki search "python"

# See what's in the vault
awiki status
```

## Commands

### `awiki init [path]`

Create a new vault at the given path (defaults to current directory). Sets up the directory structure, default topics, and saves the vault location to `~/.config/agent-wiki/config.yaml`.

```bash
awiki init ~/vaults/agent-wiki
```

### `awiki ingest <files> [--topic <topic>] [--tags <tags>]`

Ingest one or more files into the vault. Each file is copied to `raw/` (immutable archive) and a wiki page is created in the appropriate topic folder with YAML frontmatter.

```bash
awiki ingest notes.md                          # uses default topic (research)
awiki ingest notes.md --topic tools --tags cli,python
awiki ingest *.md --topic research              # glob support
```

- Title is extracted from the first `# heading`, or derived from the filename.
- The original file is preserved in `raw/` and never modified.

### `awiki search <query> [--topic <topic>]`

Full-text search across all wiki pages. Uses ripgrep if available, otherwise Python regex. Skips `raw/`, `index.md`, and `log.md`.

```bash
awiki search "authentication"
awiki search "docker" --topic tools
```

### `awiki index`

Rebuild `index.md` from all wiki pages, grouped by topic. Each entry shows the page title (as a wikilink), file path, tags, and last updated date.

```bash
awiki index
```

### `awiki lint`

Audit the vault for issues:

- **LINK** — broken `[[wikilinks]]` pointing to pages that don't exist
- **ORPHAN** — pages with no incoming wikilinks
- **RAW** — files in `raw/` that have no corresponding wiki page
- **META** — pages missing YAML frontmatter

```bash
awiki lint
```

### `awiki status`

Show a vault overview: page count per topic, raw file count, and last activity.

```bash
awiki status
```

### `awiki log [--last N]`

Show the activity log. Each ingest and maintenance action is recorded with a timestamp.

```bash
awiki log
awiki log --last 5
```

## Claude Code Skills

Three skills provide agent integration. Install them by adding the `skills/` directory to your Claude Code configuration.

### `/awiki-search`

Search the wiki before resorting to web searches. The agent runs `awiki search` and presents results.

### `/awiki-save`

Save content from the current conversation to the wiki. Two-step process:

1. The agent generates a markdown summary of the conversation content
2. The summary is ingested into the vault via `awiki ingest`

The agent asks for topic and tags if not obvious from context.

### `/awiki-ingest`

Ingest an existing file into the vault. The agent confirms the file path, asks for topic/tags, and runs `awiki ingest`.

### CLAUDE.md Integration

Add this to your project or global CLAUDE.md to make the agent check the wiki automatically:

```markdown
## Agent Wiki
Before web searching for technical knowledge, search the wiki first:
`awiki search <query>`
The wiki vault is at the path configured in ~/.config/agent-wiki/config.yaml.
```

## Architecture

The system follows Karpathy's three-layer architecture:

```
                   You / Your Agent
                         |
                    +-----------+
                    |  CLI/Skills|  awiki commands + Claude Code skills
                    +-----------+
                         |
          +--------------+--------------+
          |              |              |
     +--------+    +----------+    +--------+
     |  Raw   |    |   Wiki   |    | Config |
     | Sources|    |  Pages   |    | Schema |
     +--------+    +----------+    +--------+
     Immutable     Markdown +      wiki.yaml
     input docs    frontmatter +   config.yaml
                   [[wikilinks]]
```

**Raw Sources** (`raw/`) — Immutable input documents. Ingest copies files here. Nothing modifies them.

**Wiki Pages** (topic folders) — Markdown files with YAML frontmatter, organized by topic. Pages use `[[wikilinks]]` for cross-references. The agent and CLI create and update these.

**Config/Schema** — `wiki.yaml` inside the vault defines topics and metadata. `~/.config/agent-wiki/config.yaml` stores the vault location.

### Vault Structure

```
~/vaults/agent-wiki/
  wiki.yaml              # vault config: topics, name, version
  index.md               # auto-generated page index (awiki index)
  log.md                 # append-only activity log
  raw/                   # immutable source documents
  projects/              # topic folder (supports arbitrary nesting)
  decisions/             # topic folder
  research/              # topic folder
  tools/                 # topic folder
```

### Wiki Page Format

```markdown
---
title: ViewPoint API v3
topic: projects
tags: [viewpoint, api, php]
created: 2026-04-14
updated: 2026-04-14
sources: [raw/viewpoint-api-notes.md]
---

# ViewPoint API v3

REST API serving the mobile app and external integrations.
See [[VP Token System]] for authentication details.
```

### Project Structure

```
agent-wiki/
  src/agent_wiki/
    cli.py         # Click CLI with all commands
    config.py      # User and vault config loading
    vault.py       # Vault initialization
    ingest.py      # File ingestion to raw/ + wiki page creation
    search.py      # Full-text search (ripgrep + Python fallback)
    index.py       # Rebuild index.md
    lint.py        # Audit broken links, orphans, missing frontmatter
    log.py         # Append-only activity log
    page.py        # Page model: frontmatter, slugify, wikilinks
  skills/
    awiki-search/  # Claude Code skill
    awiki-save/    # Claude Code skill
    awiki-ingest/  # Claude Code skill
  tests/           # pytest test suite (39 tests)
```

### Obsidian / Logseq Compatibility

The vault is compatible with Obsidian and Logseq out of the box:

- Markdown files with YAML frontmatter
- `[[wikilinks]]` for cross-references
- Standard folder structure

Open the vault directory in Obsidian as a vault and everything just works.

## Configuration

### User config (`~/.config/agent-wiki/config.yaml`)

```yaml
vault_path: ~/vaults/agent-wiki
```

Set automatically by `awiki init`. Override to point to a different vault.

The config directory can be overridden with the `AGENT_WIKI_CONFIG_DIR` environment variable.

### Vault config (`wiki.yaml`)

```yaml
vault:
  name: agent-wiki
  version: 1
topics:
  - projects
  - decisions
  - research
  - tools
default_topic: research
```

Add new topics by editing this file and creating the corresponding directory.

## Future Roadmap

These are planned but not yet implemented:

- **Smart ingestion** — Use `claude -p` or a local model to extract entities, generate cross-references, and decide which existing pages to update during ingest
- **Shared wikis** — Sync topics between personal and team wikis, with per-topic or per-user read/write permissions
- **Additional agent support** — AGENTS.md and wrappers for Cursor, Codex, and other agents
- **Research agent** — A dedicated agent that combines wiki search with web search, filing results back into the wiki automatically
- **Auto-capture** — Agent notices high-value findings during conversations and suggests saving them (with user confirmation)
- **Conversation summarization** — Generate structured summaries from conversation transcripts for ingestion
- **Vault viewer** — Lightweight web UI for browsing the vault without Obsidian

## Running Tests

```bash
cd agent-wiki
source .venv/bin/activate
python -m pytest -v
```
