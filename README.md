# Agent Wiki

**AI agents earn hard-won knowledge in every conversation — then lose it the moment the session ends.** Agent Wiki is the memory they keep instead: a single plain-markdown vault your agents search *before* reaching for the web, and write back to whenever they learn something worth keeping. One small CLI (`awiki`) is the only door in, so the same commands work whether the vault is a local folder or a server shared across every project and machine you point at it. And underneath it's just files — grep it, open it in Obsidian, script it in Python. No database, no lock-in. (Inspired by [Karpathy's LLM wiki concept](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).)

**Get started in one line:** point your agent at this repo and tell it to run **`awiki guide`** — it writes its own usage instructions into your `CLAUDE.md` / `AGENTS.md` and starts searching the wiki before it reaches for the web.

## Features

- **Plain markdown, no database** — the whole vault is files with YAML frontmatter and `[[wikilinks]]`. `cat` it, `grep` it, open it in Obsidian/Logseq, or script against it in Python. Nothing to run.
- **OKF-aligned format** — the vault is ~80% conformant with Google's [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/) by convergent design (markdown + frontmatter, generated `index.md` / `log.md`, no prescribed taxonomy, untyped links, arbitrary frontmatter keys preserved).
- **Ingest files _and_ URLs** — copy a file or fetch a web page into an immutable `raw/` archive (HTML via trafilatura, PDFs supported). Every ingest writes a sha256 **provenance sidecar** so drift is detectable.
- **Edit-the-raw, re-ingest** — pages are *rendered* from their `raw/` source. Edit the source and `awiki reingest`; if a page has drifted, you get a diff instead of a silent clobber.
- **Fast search + full-page read** — multi-word AND search with coverage ranking (ripgrep-backed), then `awiki show <path>` prints any page verbatim.
- **Conversation capture** — adapters pull Claude Code and OpenCode sessions (plus a drop-zone for any agent) into the vault, with optional LLM summarization.
- **Auto-context hook** — a `UserPromptSubmit` hook silently surfaces relevant pages to your agent on every prompt, so it knows what it already knows.
- **Tag vocabulary** — an optional, CLI-managed vocabulary canonicalizes tags (aliases → preferred), with `awiki tag fix` and a lint-based CI gate.
- **Vault linting** — audit broken links, orphans, raw/page drift, stale pages, oversized pages, index gaps, and tag issues in one pass.
- **Network vault** — `awiki serve` shares one vault over HTTP; remote machines use the **same `awiki` CLI** transparently, with bearer-token auth and reader/writer/admin roles.
- **Agent-first integration** — agent skills plus a self-installing `awiki guide` block that teaches any agent (via `CLAUDE.md` / `AGENTS.md`) to search the wiki first and save what's worth keeping.

## Vision

Agents accumulate knowledge across conversations but lose it when the session ends. Agent Wiki is a persistent, compounding knowledge store that agents maintain alongside you. The human curates sources and asks questions; the agent handles the bookkeeping — filing research, maintaining cross-references, surfacing relevant knowledge before reaching for the web.

The wiki is designed to be agent-first but human-readable. It's plain markdown files you can browse in Obsidian, grep from a terminal, or script against with Python. No databases, no servers, no lock-in.

Agents reach the vault through a single, narrow door: the `awiki` CLI. That indirection is deliberate. The very same command works whether the vault is a local folder or a server across the network — point the CLI at a remote vault and every command transparently forwards over HTTP, so a laptop, an edge assistant, and a workstation can all read and write one shared brain with identical commands.

It's also a safeguard. Because the CLI is the only way in, it enforces the vault's invariants no matter who's driving: `raw/` sources stay immutable, pages are *re-rendered* from their source rather than hand-edited, drift is surfaced as a diff instead of a silent clobber, and remote access is gated by reader/writer/admin tokens. A less capable local model doesn't need to understand the vault's layout or be trusted to edit markdown by hand — it calls a handful of commands and the guardrails hold, while the heavier reasoning can live on a bigger model or the vault host.

Onboarding an agent is one command: **`awiki guide`** prints a self-installing usage block. Point your agent at it and it adapts the wording into your `CLAUDE.md` / `AGENTS.md`, teaching the agent to search the wiki first, read full pages, and save what's worth keeping — and to re-sync itself when a newer version ships.

## Installation

Requires Python 3.10+.

From PyPI (the distribution is `agent-wiki-kb`; it installs the `awiki` command):

```bash
pip install agent-wiki-kb
```

Or from source, for development:

```bash
cd agent-wiki
uv venv && source .venv/bin/activate
uv pip install -e .
```

Either way you get two commands: `awiki` and `aw` (short alias). They are identical.

If your agent uses a skill-capable harness (Claude Code, pi, Hermes, opencode), install the bundled skills so it can search and save to the wiki:

```bash
awiki skills install
```

See [Agent Skills](#agent-skills) for the full lifecycle (`status` / `update` / `uninstall`, `--scope`, `--harness`).

### Optional

- **ripgrep** (`rg`) — search uses ripgrep when available, falls back to a built-in Python scan.
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

### `awiki ingest <files-or-urls> [--topic <topic>] [--tags <tags>] [--update] [--force] [--tag-mode off|warn|strict]`

Ingest one or more **files or URLs** into the vault. Each source is copied (or fetched) into `raw/` (immutable archive) and a wiki page is created in the appropriate topic folder with YAML frontmatter.

```bash
awiki ingest notes.md                          # uses default topic (research)
awiki ingest notes.md --topic tools --tags cli,python
awiki ingest *.md --topic research              # glob support
awiki ingest https://example.com/post          # fetch + ingest a web page
awiki ingest notes.md --update                 # overwrite raw + update the linked page
```

- Title is extracted from the first `# heading`, or derived from the filename (for URLs, from the fetched page's title, then the URL).
- The original source is preserved in `raw/` and never modified.
- Every ingest writes a **provenance sidecar** (`raw/<name>.meta.yaml`) recording the source, fetcher, and a sha256 of the raw body. `awiki lint` uses this to detect drift.
- A file is identified by its `raw/` basename. **Without `--update`, ingesting a source whose basename already exists in `raw/` is refused** so nothing is silently clobbered. In a glob, the colliding file is skipped and the rest proceed; the command exits non-zero if any file was skipped.
- `--update` overwrites `raw/<basename>` from an **external** source and rewrites its linked wiki page (located via the page's `sources:` frontmatter): the body is refreshed, `created` is preserved, `updated` is bumped, and tags are kept unless `--tags` is given. If the title or `--topic` changed, the page file is renamed/moved to match. (To rebuild a page after editing the vault's *own* `raw/` copy, use [`awiki reingest`](#awiki-reingest-name) instead.)
- `--force` proceeds even when the target page has diverged from its `raw/` source (otherwise ingest shows the diff and stops).
- `--tag-mode off|warn|strict` forces the tag-vocabulary mode for this ingest only (see [`awiki tag`](#awiki-tag-addsuggestfix)); it does not change the vault's configured mode.

#### Ingesting URLs

`awiki ingest <url>` fetches the page and ingests it like any other source:

- **HTML** is extracted to clean markdown via [trafilatura](https://trafilatura.readthedocs.io/); **PDFs** via `pymupdf4llm` (with `pdfplumber` selectable).
- The original fetched artifact is archived byte-identically under `raw/assets/`, and the page carries an inline `source_url`.
- URLs are normalized for dedup, and a sha256 check skips re-ingesting an unchanged URL. Non-text content types are rejected with a friendly error.
- On the network server, the **client** does the fetch, so URL ingest works whether the vault is local or remote.

### `awiki reingest <name>`

Rebuild a page from its **own** `raw/<name>` source after you edit that raw file. This is the canonical page-edit loop: pages are *rendered* from `raw/`, so you never hand-edit a page in a topic folder — edit `raw/<name>`, then `awiki reingest <name>`.

```bash
# edit raw/my-notes.md, then:
awiki reingest my-notes.md
awiki reingest my-notes.md --force    # rebuild even if the page diverged from its raw
```

- The body is taken verbatim from the raw; frontmatter (title from the first `# H1`, tags, `created`) is regenerated — keep the H1 stable, or the slug (and thus the page path) changes and can orphan the page.
- If the page has diverged from its raw (e.g. someone edited the page directly), `reingest` prints a diff and stops. Fold anything worth keeping into the raw, then re-run with `--force`.
- `reingest` only propagates the raw's content into the page — it never authors the change itself. Contrast `ingest --update`, which pulls from an *external* file.
- Editing the `raw/` source never trips the drift guard — `reingest` rebuilds cleanly, no `--force` needed. The guard is only for the other case: a page that was hand-edited out of band. (Each page carries a `render_hash` of its body in frontmatter, which is how awiki tells the two apart.)

### `awiki raw <name>`

Print a page's `raw/<name>` source path — the file you actually edit before `awiki reingest`. Output goes to stdout so it drops straight into command substitution; pair it with `reingest` for the whole edit loop in two commands.

```bash
$EDITOR "$(awiki raw my-notes.md)"     # open the raw source
awiki reingest my-notes.md             # re-render the page from it
```

- Errors exactly as `reingest` does on a missing or ambiguous `<name>`.
- On a **remote** vault the raw lives on the server: `raw` prints the server-side reference and notes on stderr that it isn't directly editable from the client.

### `awiki search <query> [--topic <topic>] [--limit N]`

Full-text search across all wiki pages. Multi-word queries match **all** terms anywhere in a page (AND across the page); pages matching only some terms are listed separately as lower-ranked partial matches, ordered by how many terms they cover. Uses ripgrep when available, otherwise a built-in case-insensitive substring scan. Skips `raw/`, `index.md`, and `log.md`. `--limit` caps how many all-terms results are shown (default 20).

```bash
awiki search "authentication"
awiki search "claude code hooks"        # pages containing all three terms rank first
awiki search "docker" --topic tools
```

### `awiki show <path>`

Print a wiki page (or any file in the vault) by its **vault-relative path** — the path `awiki search` prints in its results. Output is the file verbatim, including YAML frontmatter. Use it to read a full page after locating it with `awiki search`.

```bash
awiki search "raft consensus"            # prints e.g. research/raft-consensus.md
awiki show research/raft-consensus.md    # prints that page in full
```

- Accepts any file inside the vault (topic pages, `raw/`, `index.md`, `log.md`).
- Paths that escape the vault are rejected; missing files and binary (non-UTF-8) files report an error and exit non-zero.

### `awiki index`

Rebuild `index.md` from all wiki pages, grouped by topic. Each entry shows the page title (as a wikilink), file path, tags, and last updated date.

```bash
awiki index
```

### `awiki lint [--strict] [--refetch]`

Audit the vault for issues:

- **LINK** — broken `[[wikilinks]]` pointing to pages that don't exist
- **ORPHAN** — pages with no incoming wikilinks
- **RAW** — files in `raw/` that have no corresponding wiki page
- **META** — pages missing YAML frontmatter
- **DRIFT** — a page whose body no longer matches its `raw/` source
- **SOURCE** — a `raw/` file edited in place (drifted from its recorded sha256)
- **STALE** — a page whose body lags its newest source
- **SIZE** — pages over 200 lines, flagged as split candidates
- **INDEX** — pages missing from `index.md`
- **TAG** — tag-audit findings: alias-fixable or novel tags, or vocabulary conflicts (only when a `tags:` vocabulary is configured)
- **UPSTREAM** — a URL source whose upstream content changed (only with `--refetch`)

```bash
awiki lint
awiki lint --strict      # CI gate: exit non-zero if any TAG finding exists
awiki lint --refetch     # also re-fetch URL sources and flag upstream changes (network; local vaults only)
```

### `awiki tag add|suggest|fix`

Manage an optional **tag vocabulary** that canonicalizes tags across the vault. The vocabulary lives in a `tags:` block in `wiki.yaml` (`mode: off | warn | strict` plus a preferred → aliases map — see [Vault config](#vault-config-wikiyaml)). It is inert until configured.

```bash
awiki tag add cli --alias command-line --alias commandline   # persist a preferred term + aliases
awiki tag suggest                                            # draft a vocabulary from tags already in use
awiki tag suggest --write                                    # merge that draft into wiki.yaml
awiki tag fix                                                # preview: which pages' tags would canonicalize
awiki tag fix --write                                       # apply: rewrite page frontmatter tags in place
awiki tag fix --topic research                              # narrow to one topic (or pass a path)
```

- **`tag add`** appends a preferred term (and optional `--alias` entries, repeatable) to the vocabulary via a comment-preserving `wiki.yaml` writer. Idempotent; refuses to bind an alias already claimed by another term.
- **`tag suggest`** scans every page, counts tag frequencies, and prints a valid `tags:` block covering all in-use tags, grouping related tags as alias candidates. `--write` merges it into `wiki.yaml`. String heuristics only, no ML.
- **`tag fix`** canonicalizes existing pages' frontmatter tags against the vocabulary — aliases are rewritten to their preferred term; novel out-of-vocabulary tags are reported but left for you (adopt via `tag add`, or remove). Preview by default; `--write` rewrites **frontmatter only** — the page body stays byte-identical and `raw/` is never touched.

Ingest also applies the vocabulary at write time; `awiki lint` reports fixable/novel tags as **TAG** findings, and `awiki lint --strict` gates on them.

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

### `awiki sync [--source cc|opencode|drop-zone] [--since DATE] [--dry-run]`

Ingest conversations from configured sources. See the [Ingesting conversations](#ingesting-conversations) section below.

### `awiki doctor [--fix] [--dry-run] [--reconcile-raw]`

Inspect the vault for drift from the current schema and offer to fix each finding. Run this after upgrading `awiki`: it adds missing `wiki.yaml` sections (`conversations`, `summarizer`, `sources`), missing topic dirs, missing `raw/sessions/` + `incoming/`, and warns when an enabled source points at a path that doesn't exist. Interactive by default — use `--fix` to apply everything, `--dry-run` to just report.

`doctor` also reports **raw content drift** — pages whose body no longer matches their `raw/` source (e.g. after editing a page directly). Rewriting raw from the canonical page is a deliberate, **server-local** operation: run `awiki doctor --reconcile-raw` on the machine that holds the vault. It is excluded from a blanket `--fix`, prompts before overwriting (default No), and **cannot be triggered by a remote client** — there is no HTTP path for it.

### `awiki adapt <source> <path-or-id> [-o FILE]`

Low-level: convert a single session into a conversation bundle without ingesting. Useful for hooks, scripts, and debugging an adapter.

### `awiki ingest-conversation <bundle.md>`

Ingest a single bundle file. The entry point for external producers (e.g. a personal assistant) that write bundles directly rather than using an adapter.

### `awiki context [--output-format claude-json|plain]`

Auto-context hook payload. Reads `{"prompt": "..."}` JSON from stdin, extracts keywords with YAKE, searches the vault, and emits a compact pointer block to stdout. Designed to be wired into an agent CLI's `UserPromptSubmit`-style hook so the model sees relevant page titles before answering. Silent-fails on every error path — never blocks the prompt.

```bash
echo '{"prompt":"how do I configure ingest"}' | awiki context
# {"hookSpecificOutput": {"additionalContext": "<!-- agent-wiki: 3 possibly-relevant pages... -->\n## research\n- [Ingest Pipeline](research/ingest-pipeline.md)\n..."}}
```

Skips short prompts (< 15 chars or < 3 words) and slash commands. Toggleable via `auto_context: true|false` in `wiki.yaml` or `AWIKI_AUTO_CONTEXT=0|1` env var. Diagnostics go to `~/.cache/agent-wiki/context.log`.

### `awiki hook install|uninstall|status [--agent claude|manual] [--config-path PATH]`

Wire `awiki context` into an agent CLI's hook system.

```bash
awiki hook install --agent claude       # edits ~/.claude/settings.json (atomic, idempotent)
awiki hook status --agent claude        # report install state
awiki hook uninstall --agent claude     # remove only the awiki entry, preserve others
awiki hook install --agent manual       # print copy-paste wiring for any host
```

The Claude backend writes a `UserPromptSubmit` hook entry pointing at `awiki context`, preserves all other settings, and refuses to mutate the file if it isn't valid JSON. Use `--config-path` to target a non-default settings file (handy for tests). The `manual` backend touches no files — it just prints the contract so you can wire OpenCode, Codex, Cursor, etc. by hand.

## Ingesting conversations

Conversations are pulled in via **adapters** that turn agent-native session stores into a canonical **Conversation Bundle** (a single markdown file with frontmatter, stored under `raw/sessions/`). Bundles are then ingested into the `sessions` topic like any other wiki page.

Three adapters ship today:

- **claude-code** — reads Claude Code JSONL transcripts from `~/.claude/projects/<slug>/*.jsonl`.
- **opencode** — reads Opencode's SQLite store at `~/.local/share/opencode/opencode.db` (opened read-only).
- **drop-zone** — picks up pre-written bundles from a configured directory (default: `<vault>/incoming/`). This is how external agents without a built-in adapter (e.g. a personal assistant) file conversations.

Run the sync manually:

```bash
awiki sync                           # all enabled sources
awiki sync --source claude-code      # one source only
awiki sync --dry-run                 # show what would be added
awiki sync --since 2026-04-01        # older stuff only
```

Sync is state-tracked in `<vault>/.awiki-sync-state.json`, so reruns are idempotent. A changed session (mtime or content hash) gets re-ingested; unchanged sessions are skipped.

### Writing bundles directly (for external agents)

See [`Doc/conversation-bundle-schema.md`](Doc/conversation-bundle-schema.md) for the full spec. Minimum bundle:

```markdown
---
type: conversation
agent: my-assistant
session_id: 2026-04-18-1030
title: "Quick question about sqlite locking"
---

# Quick question about sqlite locking

## user
…

## assistant
…
```

Drop it into `<vault>/incoming/` (or whatever `sources.drop_zone.path` points to) and the next `awiki sync` will move it into `raw/sessions/` and create a wiki page under `sessions/`. Malformed bundles are quarantined under `incoming/rejected/` with a `.reason` sidecar — nothing is silently dropped.

### Summarization (optional)

By default the wiki page for a conversation is a link back to the full transcript in `raw/sessions/`. You can swap in a summarizer via `wiki.yaml`:

```yaml
summarizer:
  type: none           # none | claude-p | local-openai
  claude_p:
    args: ["-p"]
  local_openai:
    base_url: http://127.0.0.1:8080/v1
    model: ""
    max_tokens: 600
```

- `none` — no LLM calls. Fast, offline, zero external deps.
- `claude-p` — shells out to the `claude` CLI in `-p` mode. Uses your existing Claude Code credentials; no API key.
- `local-openai` — POSTs to any OpenAI-compatible endpoint (e.g. a `llama.cpp` server). Stays local; no external traffic.

If a summarizer is configured, each wiki page body is replaced with a structured summary (Context / Decisions / Key Exchanges / Open Threads). The raw transcript stays in `raw/sessions/` regardless.

## Agent Skills

Three skills wrap the common flows for an agent. They ship as `SKILL.md` skills (usable by Claude Code and other agents that support the format), and because each one just shells out to the plain `awiki` CLI, any agent that can run a shell command can offer the same flows even without native skill support. They ride inside the wheel as package data under `src/agent_wiki/skills/`; install them with `awiki skills install`, which copies all three into whatever agent harness it detects (Claude Code, pi, Hermes, opencode). Use `awiki skills status` / `update` / `uninstall` to manage them, and `--scope project` to install into the current project instead of your user config.

```bash
awiki skills install                    # install all three into every detected harness (user scope)
awiki skills install --scope project    # install into the current project, not your user config
awiki skills install --harness claude   # limit to one detected harness
awiki skills status                     # show each skill's state per harness
awiki skills update                     # refresh stale installs to the bundled version
awiki skills uninstall                  # remove the skills this package installed
```

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

To make agents check the wiki automatically, add wiki guidance to your project or global agent-memory file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, …). Run:

```bash
awiki guide          # prints a self-installing block: tell your agent to run this
awiki guide --raw    # prints just the block, for pasting into a memory file by hand
```

`awiki guide` emits an agent-directed header plus a canonical wiki-usage block wrapped in `<!-- awiki:begin vX.Y.Z -->` / `<!-- awiki:end -->` markers. Point an agent at it ("set up awiki usage instructions") and it will adapt the wording to your project and append the block to the right memory file; the markers make re-running idempotent. The block tells the agent to search the wiki first, read full pages with `awiki show <path>`, and save findings with the `awiki-save` skill.

The begin marker carries the awiki version, and the block ends with a note telling the agent to re-run `awiki guide` whenever `awiki --version` reports something newer than that marker. On re-run, the header tells the agent to compare versions and, if the installed block is older, *re-adapt* it — folding in the new content (and copying the literal `awiki …` commands verbatim) while preserving the project's own customizations — so the guide stays current as awiki evolves without losing local wording.

> The verb was `awiki directions` before v0.5.0; it still works as a hidden alias, but `awiki guide` is the name going forward.

### Auto-Context Hook (UserPromptSubmit)

Instead of relying on a CLAUDE.md prompt, you can have agent-wiki inject pointers to relevant pages on every user prompt:

```bash
awiki hook install --agent claude
```

This adds a `UserPromptSubmit` hook to `~/.claude/settings.json` that runs `awiki context` for each prompt. The hook extracts keywords with YAKE, searches the vault, and silently injects a small block of page pointers (capped at 5) so the model knows what's available without you having to ask. Skips slash commands and short prompts. Toggle off per-vault with `auto_context: false` in `wiki.yaml` or one-shot with `AWIKI_AUTO_CONTEXT=0`.

For other agent CLIs (OpenCode, Codex, etc.), `awiki hook install --agent manual` prints the wiring contract so you can hook it up by hand.

## Architecture

The system follows Karpathy's three-layer architecture:

```
                   You / Your Agent
                         |
                    +-----------+
                    |  CLI/Skills|  awiki commands + agent skills
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
title: Payments Service API
topic: projects
tags: [payments, api, backend]
created: 2026-04-14
updated: 2026-04-14
sources: [raw/payments-service-notes.md]
---

# Payments Service API

REST API serving the mobile app and external integrations.
See [[Auth Tokens]] for authentication details.
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
    context.py     # Auto-context hook: keyword extract, search, format
    hooks/         # Per-agent install backends (claude, manual)
    skills/        # Bundled agent skills (package data; `awiki skills install`)
      awiki-search/  # agent skill
      awiki-save/    # agent skill
      awiki-ingest/  # agent skill
  tests/           # pytest test suite
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

To use a different vault for a **single invocation** without touching the config, pass `--vault PATH` (or set `AWIKI_VAULT`). This forces a local vault and overrides both the configured local and remote settings for that one command:

```bash
awiki --vault /tmp/scratch-vault status
AWIKI_VAULT=~/vaults/other awiki search "raft"
```

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

# Optional: a tag vocabulary that canonicalizes tags across the vault.
tags:
  mode: warn            # off | warn | strict
  cli:                  # preferred term …
    - command-line      # … and its aliases
    - commandline
```

Add new topics by **editing this file and creating the corresponding directory** — the `topics` / `default_topic` keys are hand-edited.

The `tags:` block is different: it has a sanctioned CLI write path. Manage it with [`awiki tag add`](#awiki-tag-addsuggestfix) and `awiki tag suggest --write` (both use a comment-preserving writer) rather than editing it by hand.

## Network server

A vault can be served over HTTP so a remote AI assistant reads from and contributes
to the same vault using the **same `awiki` CLI** as a transparent client. The CLI
talks to a `VaultService` facade; locally that's an in-process call, remotely it's
an HTTP round-trip — every command behaves the same either way.

### Server host

The HTTP server ships in the default install — no extra to install. Add a token
and run the daemon:

```bash
awiki token add laptop --role admin   # prints the secret ONCE; only its hash is stored
awiki serve                           # binds 127.0.0.1:8731 by default
```

Tokens live in `~/.config/agent-wiki/server.yaml` (respects `AGENT_WIKI_CONFIG_DIR`),
**never** in the vault. Manage them with:

```bash
awiki token add <name> --role reader|writer|admin
awiki token list      # names + roles only, never secrets
awiki token revoke <name>
```

Roles are ranked `reader < writer < admin`: readers can search/show/status/log/lint/context,
writers can additionally ingest/index/sync/adapt, and `doctor` requires admin.

### Running as a service (systemd)

A sample unit ships at [`agent-wiki.service`](agent-wiki.service):

```ini
[Unit]
Description=Agent Wiki server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/awiki serve --bind 127.0.0.1 --port 8731
Restart=on-failure
User=youruser

[Install]
WantedBy=multi-user.target
```

Install it as a system service:

```bash
sudo cp agent-wiki.service /etc/systemd/system/
sudoedit /etc/systemd/system/agent-wiki.service   # set User= and the ExecStart path
sudo systemctl daemon-reload
sudo systemctl enable --now agent-wiki
sudo systemctl status agent-wiki                  # confirm it's running
```

Edit `User=` to the account that owns the vault, and point `ExecStart` at your
installed `awiki` binary — run `which awiki` to find it (inside a uv venv it lives
in `.venv/bin/awiki`, not `/usr/local/bin`). Keep `--bind`/`--port` consistent with
how you terminate TLS in front of it (see below).

### TLS

`awiki serve` speaks plain HTTP and binds to loopback by default. For remote access,
terminate TLS at a reverse proxy (nginx/Caddy) in front of it and point clients at the
HTTPS URL — the server itself does not manage certificates.

### Remote client

On another machine, point the CLI at the server:

```bash
awiki init --remote https://wiki.example.com --token <secret>
awiki search "raft consensus"   # transparently runs over HTTP
awiki init --clear              # drop the remote config from this client
```

Local and remote vaults are mutually exclusive: setting one clears the other.

### Switching between local and remote

Switching only rewrites the client config at `~/.config/agent-wiki/config.yaml` —
it never reads, moves, or deletes vault files. Pointing a machine that already has a
local vault at a remote server is safe: the local vault stays on disk untouched, and
`awiki` simply routes commands to the server instead.

One sharp edge: the config is **overwritten, not merged**, so switching to remote
drops the `vault_path` pointer, and `awiki init --clear` only removes the remote
config (it does not restore `vault_path`). Because `awiki init <path>` refuses an
existing vault ("Vault already exists"), the way back to a local vault is to put the
`vault_path` line back in `config.yaml`. Back it up before switching so the return
trip is a one-liner:

```bash
cp ~/.config/agent-wiki/config.yaml ~/.config/agent-wiki/config.yaml.local-bak
awiki init --remote https://wiki.example.com --token <secret>
# …later, to go back to the local vault:
cp ~/.config/agent-wiki/config.yaml.local-bak ~/.config/agent-wiki/config.yaml
```

Your local vault files are never destroyed by either direction of the switch — you
can also ingest them into the remote later with `awiki ingest <path-to-old-vault>/...`
(plain `ingest` uploads from the client; `sync`/`adapt` are server-side — see below).

### Server-host semantics

A few commands act on the **server's** machine, not the client's:

- `sync` and `adapt` read the *server's* configured session directories and take
  *server-side* paths/refs — they do not see files on the client.
- `hook` is always local (it wires the client's agent CLI; there is no server hook).
- Mutating operations serialize via per-vault file locks; under contention a request
  may return `503` (the CLI surfaces this as "server busy, try again").

## Future Roadmap

These are planned but not yet implemented:

- **Smart ingestion** — Use `claude -p` or a local model to extract entities, generate cross-references, and decide which existing pages to update during ingest
- **Shared wikis** — Sync topics between personal and team wikis, with per-topic or per-user read/write permissions
- **Additional agent support** — AGENTS.md and wrappers for Cursor, Codex, and other agents
- **Research agent** — A dedicated agent that combines wiki search with web search, filing results back into the wiki automatically
- **Auto-capture** — Agent notices high-value findings during conversations and suggests saving them (with user confirmation)
- **Vault viewer** — Lightweight web UI for browsing the vault without Obsidian

## Running Tests

```bash
cd agent-wiki
source .venv/bin/activate
python -m pytest -v
```
