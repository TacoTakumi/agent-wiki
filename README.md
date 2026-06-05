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

### `awiki ingest <files> [--topic <topic>] [--tags <tags>]`

Ingest one or more files into the vault. Each file is copied to `raw/` (immutable archive) and a wiki page is created in the appropriate topic folder with YAML frontmatter.

```bash
awiki ingest notes.md                          # uses default topic (research)
awiki ingest notes.md --topic tools --tags cli,python
awiki ingest *.md --topic research              # glob support
```

- Title is extracted from the first `# heading`, or derived from the filename.
- The original file is preserved in `raw/` and never modified.

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

### `awiki sync [--source cc|opencode|drop-zone] [--since DATE] [--dry-run]`

Ingest conversations from configured sources. See the [Ingesting conversations](#ingesting-conversations) section below.

### `awiki doctor [--fix] [--dry-run]`

Inspect the vault for drift from the current schema and offer to fix each finding. Run this after upgrading `awiki`: it adds missing `wiki.yaml` sections (`conversations`, `summarizer`, `sources`), missing topic dirs, missing `raw/sessions/` + `incoming/`, and warns when an enabled source points at a path that doesn't exist. Interactive by default — use `--fix` to apply everything, `--dry-run` to just report.

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

To make agents check the wiki automatically, add wiki guidance to your project or global CLAUDE.md. A ready-made, project-agnostic block lives in [`awiki-claude-md-snippet.md`](awiki-claude-md-snippet.md) — paste its contents verbatim at the end of any CLAUDE.md. It tells the agent to search the wiki first, read full pages with `awiki show <path>`, and save findings with the `awiki-save` skill.

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
    context.py     # Auto-context hook: keyword extract, search, format
    hooks/         # Per-agent install backends (claude, manual)
  skills/
    awiki-search/  # Claude Code skill
    awiki-save/    # Claude Code skill
    awiki-ingest/  # Claude Code skill
  tests/           # pytest test suite (167 tests)
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
User=rob

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
- **Conversation summarization** — Generate structured summaries from conversation transcripts for ingestion
- **Vault viewer** — Lightweight web UI for browsing the vault without Obsidian

## Running Tests

```bash
cd agent-wiki
source .venv/bin/activate
python -m pytest -v
```
