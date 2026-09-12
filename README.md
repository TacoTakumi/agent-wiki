# Agent Wiki

[![PyPI](https://img.shields.io/pypi/v/agent-wiki-kb)](https://pypi.org/project/agent-wiki-kb/)
[![Python versions](https://img.shields.io/pypi/pyversions/agent-wiki-kb)](https://pypi.org/project/agent-wiki-kb/)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](https://github.com/TacoTakumi/agent-wiki/blob/main/LICENSE)

**AI agents earn hard-won knowledge in every conversation, then lose it the moment
the session ends.** Agent Wiki is the memory they keep instead: a single
plain-markdown vault your agents search *before* reaching for the web, and write
back to whenever they learn something worth keeping. Underneath it is just files -
grep it, open it in Obsidian, script it in Python. No database, no lock-in.
(Inspired by [Karpathy's LLM wiki concept](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).)

One small CLI (`awiki`) is the only door in, so the same commands work whether the
vault is a local folder or a server shared across every project and machine you
point at it. Onboarding an agent takes one command: **`awiki guide`** prints a
self-installing block for your `CLAUDE.md` / `AGENTS.md` that teaches the agent to
search the wiki first, read full pages, and save what is worth keeping.

## Features

- **Plain markdown, no database** - the whole vault is files with YAML frontmatter and `[[wikilinks]]`. `cat` it, `grep` it, open it in Obsidian/Logseq, or script against it in Python. Nothing to run. See [How the vault works](#how-the-vault-works).
- **OKF-aligned format** - the vault is ~80% conformant with Google's [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/) by convergent design (markdown + frontmatter, generated `index.md` / `log.md`, no prescribed taxonomy, untyped links, arbitrary frontmatter keys preserved).
- **Ingest files _and_ URLs** - copy a file or fetch a web page into an immutable `raw/` archive (HTML via trafilatura, PDFs supported). Every ingest writes a sha256 **provenance sidecar** so drift is detectable. See [`awiki ingest`](#awiki-ingest-files-or-urls).
- **Edit-the-raw, re-ingest** - pages are *rendered* from their `raw/` source. Edit the source and `awiki reingest`; if a page has drifted, you get a diff instead of a silent clobber. See [`awiki reingest`](#awiki-reingest-name).
- **Fast search + full-page read** - multi-word AND search with coverage ranking (ripgrep-backed), then `awiki show <path>` prints any page verbatim. See [`awiki search`](#awiki-search-query).
- **Ongoing session ingestion** - every time Claude Code, pi, or OpenCode starts, a one-line hook kicks off a detached, lock-safe `awiki sync` that files every finished session from all three agents into the vault. Set it up once per agent; never run sync by hand again. See [Ongoing session ingestion](#ongoing-session-ingestion).
- **Conversation capture** - adapters pull Claude Code, pi, and OpenCode sessions (plus a drop-zone for any agent) into the vault, with optional LLM summarization. See [Ingesting conversations](#ingesting-conversations).
- **Auto-context hook** - a `UserPromptSubmit` hook silently surfaces relevant pages to your agent on every prompt, so it knows what it already knows. See [Auto-context hook](#auto-context-hook).
- **Tag vocabulary** - an optional, CLI-managed vocabulary canonicalizes tags (aliases to preferred), with `awiki tag fix` and a lint-based CI gate. See [`awiki tag`](#awiki-tag-addsuggestfix).
- **Vault linting** - audit broken links, orphans, raw/page drift, stale pages, oversized pages, index gaps, and tag issues in one pass. See [`awiki lint`](#awiki-lint).
- **Network vault** - `awiki serve` shares one vault over HTTP; remote machines use the **same `awiki` CLI** transparently, with bearer-token auth and reader/writer/admin roles. See [Network server](#network-server).
- **Multiple named vaults** - one config registers many vaults (local paths or remote URLs); reads span them all with `vault:`-qualified results, writes stay narrow, and a repo can carry its own trust-gated `.agent-wiki/config.yaml`. See [Multiple vaults](#multiple-vaults).
- **Agent-first integration** - agent skills plus a self-installing `awiki guide` block that teaches any agent (via `CLAUDE.md` / `AGENTS.md`) to search the wiki first and save what is worth keeping. See [Using awiki with your agent](#using-awiki-with-your-agent).

## Quick start

Requires Python 3.10+. The distribution is `agent-wiki-kb`; it installs two
identical commands, `awiki` and `aw`.

```bash
pip install agent-wiki-kb    # or: uv tool install agent-wiki-kb / pipx install agent-wiki-kb
```

Then set up the machine you want to work on:

```bash
awiki init ~/vaults/agent-wiki     # create the vault
awiki skills install               # install the bundled skills into your agent harness(es)
awiki hook install --agent claude  # sessions flow in on every start; relevant pages on every prompt
```

Also using pi or OpenCode? `awiki hook install --agent pi` and
`awiki hook install --agent opencode` wire those in the same way. See
[Ongoing session ingestion](#ongoing-session-ingestion).

A vault is not per-repo. One vault serves every project you work on, and it can
serve more than one machine too: `awiki serve` shares it over HTTP, and remote
machines drive it through the same `awiki` commands. So `awiki init <path>` here
creates a local vault, and `awiki init --remote <url> --token <secret>` on
another machine points at this one instead. See
[Network server](#network-server). Prove it works:

```bash
awiki ingest my-notes.md --topic research --tags python,testing
awiki search "python testing"
awiki show research/my-notes.md
awiki status
```

That is the whole setup. Start your coding agent and say:

> We use awiki here. Run `awiki guide` and wire it into this project's memory file.

## Ongoing session ingestion

Every conversation your agents have is a session transcript sitting in some
per-agent store. Agent Wiki keeps those flowing into the vault automatically: any
agent starting on the machine triggers a background sweep that ingests every
finished session from every source, so the `sessions` topic stays current
without you ever running `awiki sync` by hand. One command per agent:

```bash
awiki hook install --agent claude    # Claude Code: SessionStart hook in ~/.claude/settings.json
awiki hook install --agent pi        # pi: extension in ~/.pi/agent/extensions/
awiki hook install --agent opencode  # OpenCode: plugin in ~/.config/opencode/plugins/
```

Each hook runs `awiki sync --detach` when the agent starts. That command returns
in well under a second: the sync itself runs in a detached background process,
consults its state file before parsing anything (an unchanged session costs one
`stat`), and writes its log to the awiki state dir
(`~/.local/state/agent-wiki/locks/<vault-digest>/sync.log`; the command prints
the exact path on stderr, and nothing on stdout, so an agent's hook output stays
clean). The log holds the latest run; if another sweep already holds the vault
lock, the new one appends a one-line "already running" notice and exits at once
instead of queueing, so several agents starting together are safe. The sweep
targets the default vault; narrow it with `--vault`. When the default vault is
remote, the sweep is a no-op on this machine: the server owns its session
sources, so run the sweep there.

Why startup and not session end? No agent offers a reliable "session finished"
signal (Claude Code's `SessionEnd` has a 1.5 s budget and skips on hangup;
OpenCode has no exit event), but the next agent start is guaranteed. Sessions
modified in the last 60 minutes are treated as live and picked up on a later
sweep. Run `awiki sync` once by hand after installing to catch up on history -
Claude Code prunes transcripts after 30 days by default.

`awiki hook install --agent claude` also installs the
[auto-context hook](#auto-context-hook); `--only sweep` or `--only context`
narrows to one. `awiki hook status` shows each hook's state and
`awiki hook uninstall` removes only what awiki wrote. Use `--agent manual` to
print the wiring for any other host.

## Using awiki with your agent

The installed skills make your agent *able* to use the wiki; a note in the
project memory file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, ...) makes it
*routine*. `awiki guide` prints that note, wrapped in a short header addressed to
the agent telling it to add the block once and leave it alone if it is already
there:

```bash
awiki guide          # the agent-directed header plus the block: tell your agent to run this
awiki guide --raw    # just the block, for pasting into a memory file by hand
```

Point an agent at it ("set up awiki usage instructions") and it will adapt the
wording to your project's name, domain, and the surrounding file's tone, then
append it to the right memory file. The `awiki ...` commands and skill names are
literal and get copied verbatim. The block is static - there are no version
markers and nothing to keep in sync as awiki evolves.

This is the block:

```markdown
## Knowledge base: the Agent Wiki (awiki)

Durable, hard-won knowledge - decisions, research, tool/config details, fixes - lives in the
**Agent Wiki**, one markdown vault **shared across all your projects** (and the assistant itself),
reached through the `awiki` CLI. Go there first for project- or domain-specific knowledge, and
before re-deriving something that was likely figured out before; what you save there is available
from every other project.

**Search, then read the page.** `awiki search "<query>"` lists matching pages (title, path,
snippets); multi-word queries match every term, so add words to narrow. `awiki show <path>` prints
a page whole - right for most, wasteful for a long log or watch page. Slice those instead:
`--outline` lists the headings, `--section "<heading text>"` prints that one section with its
subsections, and `--head N` / `--tail N` keep the first or last N of them.

    awiki show research/postgres-tuning.md --section "Connection pooling"

**Save what's worth keeping** - a decision, a non-obvious fix, a reusable pattern - with the
**`awiki-save`** skill (or **`awiki-ingest`** to pull an existing file into the vault).

**Never hand-edit a page** - each is rendered from a source in `raw/`. To change one, edit its
`raw/<name>` source (find it with `awiki raw <name>`), then run `awiki reingest <name>` to
re-render it; no `--force` needed when only the raw changed.

If `awiki` isn't installed or no vault is configured, skip the wiki and proceed normally.
```

Prompts that map onto it:

- "Check the wiki for X." - searches, then reads the matching pages in full.
- "Save that to the wiki." - the `awiki-save` skill writes the conversation's findings into a page.
- "Pull this file into the wiki." - the `awiki-ingest` skill ingests an existing file.
- "Fix the X page." - edits `raw/<name>` and re-renders with `awiki reingest`, never the page itself.

> The verb was `awiki directions` before v0.5.0; it still works as a hidden alias,
> but `awiki guide` is the name going forward.

## Why a wiki, and why one CLI

Agent Wiki is a persistent, compounding knowledge store that agents maintain
alongside you: agent-first, but human-readable throughout. The human curates
sources and asks questions; the agent handles the bookkeeping - filing research,
maintaining cross-references, surfacing relevant knowledge before reaching for
the web.

Agents reach the vault through a single, narrow door: the `awiki` CLI. That
indirection is deliberate. The very same command works whether the vault is a
local folder or a server across the network - point the CLI at a remote vault and
every command transparently forwards over HTTP, so a laptop, an edge assistant,
and a workstation can all read and write one shared brain with identical
commands.

It is also a safeguard. Because the CLI is the only way in, it enforces the
vault's invariants no matter who is driving: `raw/` sources stay immutable, pages
are *re-rendered* from their source rather than hand-edited, drift is surfaced as
a diff instead of a silent clobber, and remote access is gated by
reader/writer/admin tokens. A less capable local model does not need to
understand the vault's layout or be trusted to edit markdown by hand - it calls a
handful of commands and the guardrails hold, while the heavier reasoning can live
on a bigger model or the vault host.

## How the vault works

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

**Raw sources** (`raw/`) - immutable input documents. Ingest copies files here.
Nothing modifies them.

**Wiki pages** (topic folders) - markdown files with YAML frontmatter, organized
by topic. Pages use `[[wikilinks]]` for cross-references. The agent and CLI create
and update these.

**Config/schema** - `wiki.yaml` inside the vault defines topics and metadata.
`~/.config/agent-wiki/config.yaml` stores the vault location.

### The edit loop

A page is *rendered* from its raw source, so you never hand-edit a page in a topic
folder. To change one, edit the raw and re-render:

```bash
$EDITOR "$(awiki raw my-notes.md)"     # open the raw source
awiki reingest my-notes.md             # re-render the page from it
```

Each page carries a `render_hash` of its body in frontmatter. Editing the raw
never trips the drift guard - `reingest` rebuilds cleanly. A page that was
hand-edited out of band does trip it, and `reingest` prints a diff and stops
rather than clobbering your edit.

### Vault structure

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

### Wiki page format

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

### Obsidian / Logseq compatibility

The vault is compatible with Obsidian and Logseq out of the box: markdown files
with YAML frontmatter, `[[wikilinks]]` for cross-references, and a standard folder
structure. Open the vault directory in Obsidian as a vault and everything just
works.

## Command reference

Two global options apply to every command:

- `awiki --version` - print the installed version and exit.
- `awiki --vault PATH` - use this vault for one invocation, overriding the configured one (also settable via `AWIKI_VAULT`). Forces a local vault. See [Configuration](#configuration).

### Setup and orientation

#### `awiki init [path]`

Create a new vault at the given path (defaults to the current directory). Sets up
the directory structure and saves the vault location to
`~/.config/agent-wiki/config.yaml`. The first vault gets the standard default
topics (projects, decisions, research, tools, sessions); pass `--topics` to
choose your own, with the first listed becoming the vault's `default_topic`.

```bash
awiki init ~/vaults/agent-wiki
awiki init ~/vaults/personal-wiki --name personal               # create AND register under a name
awiki init ~/vaults/recipes --name recipes --topics "recipes, techniques"
awiki init --remote https://wiki.example.com --token <secret>   # point at a served vault instead
awiki init --clear                                              # drop the remote config from this client
```

A vault created beside existing ones does not clone the default topics, since
topics duplicated across vaults make every unqualified `--topic` ambiguous.
Without `--topics`, such an init prompts for a topic list when run on a
terminal and otherwise creates the vault with no topics, printing a warning
that explains how to add them later; ingest into a vault with no
`default_topic` requires `--topic`.

A registered name whose vault directory has been deleted does not block
re-initing: `init --name` reclaims the stale name and repoints it at the new
vault.

Local and remote vaults are mutually exclusive: setting one clears the other. See
[Network server](#network-server) for the remote side, and
[Multiple vaults](#multiple-vaults) for named registration with `--name`.

#### `awiki vault list|add|trust`

Manage the named vault registry: `vault list` is a read-only view (name, kind,
target, reachability, declaring config, default marker), `vault add NAME
PATH|URL` registers an existing vault, and `vault trust DIR` allows a
repo-local `.agent-wiki/config.yaml` to be honored. See
[Multiple vaults](#multiple-vaults).

#### `awiki guide [--raw]`

Print the self-installing block for your agent's memory file. Runs without a
vault, so a fresh agent can run it in any repo. See
[Using awiki with your agent](#using-awiki-with-your-agent).

#### `awiki skills install|status|update|uninstall`

Install the bundled agent skills into the harnesses on your machine and keep them
current. See [Agent skills](#agent-skills).

#### `awiki hook install|uninstall|status [--agent claude|pi|opencode|manual] [--config-path PATH]` (`install`/`uninstall` also take `--only context|sweep`)

Wire awiki's two hooks into an agent CLI: the **startup sweep** (`awiki sync
--detach` on every agent start) and, where the host supports it, the
**auto-context hook** (`awiki context` on every prompt).

```bash
awiki hook install --agent claude       # SessionStart sweep + UserPromptSubmit context in ~/.claude/settings.json
awiki hook install --agent pi           # writes ~/.pi/agent/extensions/awiki-sync.ts (session_start sweep)
awiki hook install --agent opencode     # writes ~/.config/opencode/plugins/awiki-sync.js (session.created sweep)
awiki hook install --agent claude --only context   # just one of the two
awiki hook status --agent claude        # per-hook installed / not installed
awiki hook uninstall --agent claude     # remove only the awiki entries, preserve others
awiki hook install --agent manual       # print copy-paste wiring for any host
```

Every backend is idempotent and atomic. The Claude backend edits
`settings.json` in place, preserves all other keys, and refuses to touch a file
that is not valid JSON. The pi and OpenCode backends each write one
marker-tagged file and will never delete a file they did not write; pi and
OpenCode have no prompt-submit event, so `--only context` is a no-op there. Use
`--config-path` to target a non-default settings/extension/plugin file (handy
for tests). The `manual` backend touches no files - it prints the contract for
both hooks so you can wire Codex, Cursor, and others by hand. See
[Ongoing session ingestion](#ongoing-session-ingestion) and
[Auto-context hook](#auto-context-hook).

#### `awiki doctor [--fix] [--dry-run] [--reconcile-raw]`

Inspect the vault for drift from the current schema and offer to fix each finding.
Run this after upgrading `awiki`: it adds missing `wiki.yaml` sections
(`conversations`, `summarizer`, `sources`), missing topic dirs, missing
`raw/sessions/` and `incoming/`, and warns when an enabled source points at a path
that does not exist. Interactive by default - use `--fix` to apply everything,
`--dry-run` to just report.

`doctor` also reports **raw content drift** - pages whose body no longer matches
their `raw/` source (for example after editing a page directly). Rewriting raw from
the canonical page is a deliberate, **server-local** operation: run
`awiki doctor --reconcile-raw` on the machine that holds the vault. It is excluded
from a blanket `--fix`, prompts before overwriting (default No), and **cannot be
triggered by a remote client** - there is no HTTP path for it.

### Getting knowledge in

#### `awiki ingest <files-or-urls>`

`awiki ingest <files-or-urls> [--topic <topic>] [--tags <tags>] [--update] [--force] [--tag-mode off|warn|strict]`

Ingest one or more **files or URLs** into the vault. Each source is copied (or
fetched) into `raw/` (immutable archive) and a wiki page is created in the
appropriate topic folder with YAML frontmatter.

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

**Ingesting URLs.** `awiki ingest <url>` fetches the page and ingests it like any
other source:

- **HTML** is extracted to clean markdown via [trafilatura](https://trafilatura.readthedocs.io/); **PDFs** via `pymupdf4llm` (with `pdfplumber` selectable).
- The original fetched artifact is archived byte-identically under `raw/assets/`, and the page carries an inline `source_url`.
- URLs are normalized for dedup, and a sha256 check skips re-ingesting an unchanged URL. Non-text content types are rejected with a friendly error.
- On the network server, the **client** does the fetch, so URL ingest works whether the vault is local or remote.

#### `awiki reingest <name>`

Rebuild a page from its **own** `raw/<name>` source after you edit that raw file.
This is the canonical page-edit loop: pages are *rendered* from `raw/`, so you
never hand-edit a page in a topic folder.

```bash
# edit raw/my-notes.md, then:
awiki reingest my-notes.md
awiki reingest my-notes.md --force    # rebuild even if the page diverged from its raw
```

- The body is taken verbatim from the raw; frontmatter (title from the first `# H1`, tags, `created`) is regenerated - keep the H1 stable, or the slug (and thus the page path) changes and can orphan the page.
- If the page has diverged from its raw (for example someone edited the page directly), `reingest` prints a diff and stops. Fold anything worth keeping into the raw, then re-run with `--force`.
- `reingest` only propagates the raw's content into the page - it never authors the change itself. Contrast `ingest --update`, which pulls from an *external* file.
- Editing the `raw/` source never trips the drift guard - `reingest` rebuilds cleanly, no `--force` needed. The guard is only for the other case: a page that was hand-edited out of band.

#### `awiki raw <name>`

Print a page's `raw/<name>` source path - the file you actually edit before
`awiki reingest`. Output goes to stdout so it drops straight into command
substitution; pair it with `reingest` for the whole edit loop in two commands.

```bash
$EDITOR "$(awiki raw my-notes.md)"     # open the raw source
awiki reingest my-notes.md             # re-render the page from it
```

- Errors exactly as `reingest` does on a missing or ambiguous `<name>`.
- On a **remote** vault the raw lives on the server: `raw` prints the server-side reference and notes on stderr that it is not directly editable from the client.

#### `awiki sync [--source claude-code|opencode|pi|drop-zone] [--since DATE] [--include-live] [--dry-run] [--detach]`

Ingest conversations from configured sources. Sessions modified in the last 60
minutes are treated as live and skipped unless `--include-live` (or the
source's `include_live: true`) is set. `--detach` forks the sync into a
background process and returns at once (this is what the startup hooks run):
output goes to a per-vault log in the awiki state dir, and a run that finds the
vault lock held exits cleanly instead of waiting. See
[Ongoing session ingestion](#ongoing-session-ingestion) and
[Ingesting conversations](#ingesting-conversations).

#### `awiki ingest-conversation <bundle.md>`

Ingest a single bundle file. The entry point for external producers (for example a
personal assistant) that write bundles directly rather than using an adapter.

#### `awiki adapt <source> <path-or-id> [-o FILE]`

Low-level: convert a single session into a conversation bundle without ingesting.
Useful for hooks, scripts, and debugging an adapter.

### Getting knowledge out

#### `awiki search <query>`

`awiki search <query> [--topic <topic>] [--limit N]`

Full-text search across all wiki pages. Multi-word queries match **all** terms
anywhere in a page (AND across the page); pages matching only some terms are
listed separately as lower-ranked partial matches, ordered by how many terms they
cover. Uses ripgrep when available, otherwise a built-in case-insensitive
substring scan. Skips `raw/`, `index.md`, and `log.md`. `--limit` caps how many
all-terms results are shown (default 20).

```bash
awiki search "authentication"
awiki search "claude code hooks"        # pages containing all three terms rank first
awiki search "docker" --topic tools
```

#### `awiki show <path>`

Print a wiki page (or any file in the vault) by its **vault-relative path** - the
path `awiki search` prints in its results. Output is the file verbatim, including
YAML frontmatter. Use it to read a full page after locating it with `awiki search`.

```bash
awiki search "raft consensus"            # prints e.g. research/raft-consensus.md
awiki show research/raft-consensus.md    # prints that page in full
```

- Accepts any file inside the vault (topic pages, `raw/`, `index.md`, `log.md`).
- Paths that escape the vault are rejected; missing files and binary (non-UTF-8) files report an error and exit non-zero.

**Reading part of a long page.** Four flags slice the output so a long log or
watch page does not have to be read whole. Any of them drops the YAML
frontmatter, so what you get is plain markdown; without them the output stays
byte-identical to the file.

```bash
awiki show projects/feed-log.md --outline                  # just the heading lines
awiki show projects/feed-log.md --section "check log"      # one section, subsections included
awiki show projects/feed-log.md --section "check log" --head 3   # its first 3 entries
awiki show projects/feed-log.md --tail 2                   # the last 2 top-level sections
```

- `--outline` prints every heading line verbatim, in file order, and nothing else. Headings inside fenced code blocks are not counted.
- `--section TEXT` matches TEXT against each heading's text, case-insensitively, as a substring. The first match wins and prints through the line before the next heading of the same or a higher level. Other matching headings are listed on stderr; no match exits non-zero.
- `--head N` and `--tail N` keep the first or last N child sections of the `--section` selection, or of the page's top-level sections when no section is given. The heading and the text above the first child are always kept.
- `--head` and `--tail` cannot be combined with each other or with `--outline`, and N must be at least 1. `--section` and `--outline` together list the headings inside the selected section.

#### `awiki status`

Show a vault overview: page count per topic, raw file count, and last activity.

#### `awiki log [--last N]`

Show the activity log. Each ingest and maintenance action is recorded with a
timestamp.

```bash
awiki log
awiki log --last 5
```

#### `awiki index`

Rebuild `index.md` from all wiki pages, grouped by topic. Each entry shows the page
title (as a wikilink), file path, tags, and last updated date.

#### `awiki context [--output-format claude-json|plain]`

Auto-context hook payload. Reads `{"prompt": "..."}` JSON from stdin, extracts
keywords with YAKE, searches the vault, and emits a compact pointer block to
stdout. Designed to be wired into an agent CLI's `UserPromptSubmit`-style hook so
the model sees relevant page titles before answering. Silent-fails on every error
path - never blocks the prompt.

```bash
echo '{"prompt":"how do I configure ingest"}' | awiki context
# {"hookSpecificOutput": {"additionalContext": "<!-- agent-wiki: 3 possibly-relevant pages... -->\n## research\n- [Ingest Pipeline](research/ingest-pipeline.md)\n..."}}
```

Skips short prompts (under 15 characters or under 3 words) and slash commands.
Toggleable via `auto_context: true|false` in `wiki.yaml` or the
`AWIKI_AUTO_CONTEXT=0|1` env var. Diagnostics go to
`~/.cache/agent-wiki/context.log`. See [Auto-context hook](#auto-context-hook).

### Maintenance

#### `awiki lint`

`awiki lint [--strict] [--refetch]`

Audit the vault for issues:

- **LINK** - broken `[[wikilinks]]` pointing to pages that do not exist
- **ORPHAN** - pages with no incoming wikilinks
- **RAW** - files in `raw/` that have no corresponding wiki page
- **META** - pages missing YAML frontmatter
- **DRIFT** - a page whose body no longer matches its `raw/` source
- **SOURCE** - a `raw/` file edited in place (drifted from its recorded sha256)
- **STALE** - a page whose body lags its newest source
- **SIZE** - pages over `page_max_lines` (default 500), flagged as split candidates
- **INDEX** - pages missing from `index.md`
- **TAG** - tag-audit findings: alias-fixable or novel tags, or vocabulary conflicts (only when a `tags:` vocabulary is configured)
- **UPSTREAM** - a URL source whose upstream content changed (only with `--refetch`)

```bash
awiki lint
awiki lint --strict      # CI gate: exit non-zero if any TAG finding exists
awiki lint --refetch     # also re-fetch URL sources and flag upstream changes (network; local vaults only)
```

**Tuning the SIZE threshold.** The page-length that trips a **SIZE** finding is
`page_max_lines`, an optional integer in a `lint:` block in `wiki.yaml`. The
built-in default is 500 lines, counted on the page body with the frontmatter
excluded. Hand-edit the block like `topics`; a value that is not a whole number
of at least 1 makes `awiki lint` fail and name the key.

```yaml
lint:
  page_max_lines: 500
```

#### `awiki tag add|suggest|fix`

Manage an optional **tag vocabulary** that canonicalizes tags across the vault. The
vocabulary lives in a `tags:` block in `wiki.yaml` (`mode: off | warn | strict`
plus a preferred-to-aliases map - see
[Vault config](#vault-config-wikiyaml)). It is inert until configured.

```bash
awiki tag add cli --alias command-line --alias commandline   # persist a preferred term + aliases
awiki tag suggest                                            # draft a vocabulary from tags already in use
awiki tag suggest --write                                    # merge that draft into wiki.yaml
awiki tag fix                                                # preview: which pages' tags would canonicalize
awiki tag fix --write                                        # apply: rewrite page frontmatter tags in place
awiki tag fix --topic research                               # narrow to one topic (or pass a path)
```

- **`tag add`** appends a preferred term (and optional `--alias` entries, repeatable) to the vocabulary via a comment-preserving `wiki.yaml` writer. Idempotent; refuses to bind an alias already claimed by another term.
- **`tag suggest`** scans every page, counts tag frequencies, and prints a valid `tags:` block covering all in-use tags, grouping related tags as alias candidates. `--write` merges it into `wiki.yaml`. String heuristics only, no ML.
- **`tag fix`** canonicalizes existing pages' frontmatter tags against the vocabulary - aliases are rewritten to their preferred term; novel out-of-vocabulary tags are reported but left for you (adopt via `tag add`, or remove). Preview by default; `--write` rewrites **frontmatter only** - the page body stays byte-identical and `raw/` is never touched.

Ingest also applies the vocabulary at write time; `awiki lint` reports
fixable/novel tags as **TAG** findings, and `awiki lint --strict` gates on them.

### Serving a vault

#### `awiki serve [--bind HOST] [--port PORT]`

Run the HTTP server for the local vault so remote machines can use it through the
same CLI. One vault per instance: the default vault, or pick one with
`awiki --vault NAME serve --port PORT`. See [Network server](#network-server)
and [Multiple vaults](#multiple-vaults).

#### `awiki token add|list|revoke`

Manage server bearer tokens. Local and admin-only; tokens live in
`~/.config/agent-wiki/server.yaml`, never in the vault. See
[Network server](#network-server).

## Ingesting conversations

Conversations are pulled in via **adapters** that turn agent-native session stores
into a canonical **Conversation Bundle** (a single markdown file with frontmatter,
stored under `raw/sessions/`). Bundles are then ingested into the `sessions` topic
like any other wiki page.

Four adapters ship today:

- **claude-code** - reads Claude Code JSONL transcripts from `~/.claude/projects/<slug>/*.jsonl`.
- **pi** - reads pi session files from `~/.pi/agent/sessions/<cwd-slug>/<timestamp>_<uuid>.jsonl` (session format v3; one file is one session).
- **opencode** - reads OpenCode's SQLite store (opened read-only). The path comes from `sources.opencode.db_path` if set, else from `opencode db path` when the binary is on `PATH`, else `~/.local/share/opencode/opencode.db`.
- **drop-zone** - picks up pre-written bundles from a configured directory (default: `<vault>/incoming/`). This is how external agents without a built-in adapter (for example a personal assistant) file conversations.

Normally the startup hooks run the sync for you (see
[Ongoing session ingestion](#ongoing-session-ingestion)). Run it by hand for a
first catch-up or to inspect what is pending:

```bash
awiki sync                           # all enabled sources
awiki sync --source pi               # one source only
awiki sync --dry-run                 # show what would be added
awiki sync --since 2026-04-01        # older stuff only
awiki sync --include-live            # also sessions touched in the last 60 minutes
awiki sync --detach                  # what the hooks run: background, logs to the state dir
```

Sync is state-tracked in `<vault>/.awiki-sync-state.json`, so reruns are
idempotent. A changed session (mtime or content hash) gets re-ingested; unchanged
sessions are skipped without being parsed. Session pages are pointers to their
transcript, so `awiki doctor` leaves them out of its raw-drift and render-hash
checks and `--reconcile-raw` never touches `raw/sessions/`.

### Writing bundles directly (for external agents)

See [`Doc/conversation-bundle-schema.md`](https://github.com/TacoTakumi/agent-wiki/blob/main/Doc/conversation-bundle-schema.md)
for the full spec. Minimum bundle:

```markdown
---
type: conversation
agent: my-assistant
session_id: 2026-04-18-1030
title: "Quick question about sqlite locking"
---

# Quick question about sqlite locking

## user
...

## assistant
...
```

Drop it into `<vault>/incoming/` (or whatever `sources.drop_zone.path` points to)
and the next `awiki sync` will move it into `raw/sessions/` and create a wiki page
under `sessions/`. Malformed bundles are quarantined under `incoming/rejected/`
with a `.reason` sidecar - nothing is silently dropped.

### Summarization (optional)

By default the wiki page for a conversation is a link back to the full transcript
in `raw/sessions/`. You can swap in a summarizer via `wiki.yaml`:

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

- `none` - no LLM calls. Fast, offline, zero external deps.
- `claude-p` - shells out to the `claude` CLI in `-p` mode. Uses your existing Claude Code credentials; no API key.
- `local-openai` - POSTs to any OpenAI-compatible endpoint (for example a `llama.cpp` server). Stays local; no external traffic.

If a summarizer is configured, each wiki page body is replaced with a structured
summary (Context / Decisions / Key Exchanges / Open Threads). The raw transcript
stays in `raw/sessions/` regardless.

## Auto-context hook

Instead of relying on a memory-file prompt, you can have agent-wiki inject
pointers to relevant pages on every user prompt:

```bash
awiki hook install --agent claude                 # both hooks: startup sweep + auto-context
awiki hook install --agent claude --only context  # just the auto-context hook
```

This adds a `UserPromptSubmit` hook to `~/.claude/settings.json` that runs
`awiki context` for each prompt. The hook extracts keywords with YAKE, searches
the vault, and silently injects a small block of page pointers (capped at 5) so
the model knows what is available without you having to ask. Skips slash commands
and short prompts. Toggle off per-vault with `auto_context: false` in `wiki.yaml`
(local vaults only - a remote vault's server-side flag does not travel over the
wire, so the hook always includes a reachable remote vault) or one-shot with
`AWIKI_AUTO_CONTEXT=0`.

pi and OpenCode have no prompt-submit event, so this hook is Claude Code only
today. For other agent CLIs (Codex, Cursor, and so on),
`awiki hook install --agent manual` prints the wiring contract so you can hook it
up by hand.

## Agent skills

Three skills wrap the common flows for an agent. They ship as `SKILL.md` skills
(usable by Claude Code and other agents that support the format), and because each
one just shells out to the plain `awiki` CLI, any agent that can run a shell
command can offer the same flows even without native skill support. They ride
inside the wheel as package data under `src/agent_wiki/skills/`.

```bash
awiki skills install                    # install all three into every detected harness (user scope)
awiki skills install --scope project    # install into the current project, not your user config
awiki skills install --harness claude   # limit to one detected harness
awiki skills status                     # show each skill's state per harness
awiki skills update                     # refresh stale installs to the bundled version
awiki skills uninstall                  # remove the skills this package installed
```

`install` copies all three into whatever agent harness it detects (Claude Code,
pi, Hermes, opencode).

- **`/awiki-search`** - search the wiki before resorting to web searches. The agent runs `awiki search` and presents results.
- **`/awiki-save`** - save content from the current conversation to the wiki: the agent generates a markdown summary, then ingests it via `awiki ingest`, asking for topic and tags if they are not obvious from context.
- **`/awiki-ingest`** - ingest an existing file into the vault. The agent confirms the file path, asks for topic and tags, and runs `awiki ingest`.

## Configuration

### User config (`~/.config/agent-wiki/config.yaml`)

```yaml
vault_path: ~/vaults/agent-wiki
```

Set automatically by `awiki init`. Override to point to a different vault.

The config directory can be overridden with the `AGENT_WIKI_CONFIG_DIR`
environment variable.

The config can also hold a **named vault registry** instead of the single
`vault_path` (see [Multiple vaults](#multiple-vaults)):

```yaml
default_vault: work
vaults:
  work:
    path: ~/vaults/work-wiki
  personal:
    path: ~/vaults/personal-wiki
  team:
    url: https://wiki.example.com:8731
    token: <secret>
```

Each entry declares `path:` (a local vault) or `url:` plus optional `token:`
(a remote one), mixed freely. A legacy `vault_path`/`server` config keeps
working unchanged - it reads as a single vault named `main` - and config
writes stay in the legacy form until the first write that needs more (a
second vault, a named init, a remote entry), which rewrites the file to the
`vaults:` schema. The default vault is `default_vault` if set, else the vault
named `main`, else a sole configured vault. No command writes `default_vault`
and there is no `awiki use` - repoint the default by editing the file.

To use a different vault for a **single invocation** without touching the
config, pass `--vault NAME|PATH` (or set `AWIKI_VAULT`). A bare value that
matches a configured vault name narrows to that vault - including a remote
one; any other value is a config-free local vault at that path (`./` or an
absolute path forces path interpretation):

```bash
awiki --vault personal status                # a configured vault, by name
awiki --vault /tmp/scratch-vault status      # any local vault, by path
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

# Conversation sources for `awiki sync` (written by `awiki init`; edit by hand).
# Each source takes `enabled`, an optional `include_live`, and a location:
conversations:
  topic: sessions
sources:
  claude_code:
    enabled: true
    path: ~/.claude/projects
  pi:
    enabled: true
    path: ~/.pi/agent/sessions
  opencode:
    enabled: true          # no db_path: resolved via `opencode db path`, then the default
  drop_zone:
    enabled: true
    path: incoming         # relative to the vault

# Optional: a tag vocabulary that canonicalizes tags across the vault.
tags:
  mode: warn            # off | warn | strict
  cli:                  # preferred term ...
    - command-line      # ... and its aliases
    - commandline
```

Add new topics by **editing this file and creating the corresponding directory** -
the `topics` / `default_topic` keys are hand-edited (`awiki init --topics` only
seeds the initial list).

The `tags:` block is different: it has a sanctioned CLI write path. Manage it with
[`awiki tag add`](#awiki-tag-addsuggestfix) and `awiki tag suggest --write` (both
use a comment-preserving writer) rather than editing it by hand.

## Multiple vaults

One machine can hold several vaults - say a `work` wiki, a `personal` one, and
a shared `team` vault served over HTTP - all reachable from one CLI. Register
them in the user config's `vaults:` map (see
[User config](#user-config-configagent-wikiconfigyaml)) or with the CLI:

```bash
awiki init ~/vaults/personal-wiki --name personal   # create and register
awiki vault add work ~/vaults/work-wiki             # register an existing vault
awiki vault add team https://wiki.example.com:8731 --token <secret>
awiki vault list                                    # name, kind, target, reachability, default marker
```

An `init` beside an existing vault does not clone the default topics - it
prompts for (or takes `--topics`) a topic list of its own, so `--topic`
routing stays unambiguous. See [`awiki init`](#awiki-init-path).

With a single configured vault nothing changes - every command behaves and
prints exactly as before. With more than one:

- **Reads span vaults.** `awiki search` prints one merged coverage-ranked
  list; every hit path carries a `vault:` qualifier
  (`work:research/raft.md`) that pastes straight into `show`, `raw`, or
  `reingest`. Unqualified references resolve across vaults: a unique match
  wins silently, an ambiguous one is a hard error listing the qualified
  candidates. One limitation: an unqualified `raw` or `reingest` name only
  probes local vaults - a remote vault's raws live server-side and are never
  probed by name. To target a remote vault's raw, qualify the name
  (`work:notes.md`); the qualified form always reaches the named vault. The auto-context hook spans vaults too, skips unreachable
  ones, and a vault with `auto_context: false` in its `wiki.yaml` stays out
  of the hook while remaining fully searchable. The opt-out is local-only:
  the serve wire contract exposes no `auto_context` flag, so a remote
  vault's server-side `auto_context: false` cannot suppress it in the hook -
  a reachable remote vault is always included.
- **Writes stay narrow.** `ingest --topic` routes to the unique vault whose
  `wiki.yaml` declares that topic; a topic declared by two vaults is a hard
  error resolved by `--vault` or a `vault:` prefix on the topic. Everything
  else that mutates (`tag`, `sync`, `doctor` fixes) acts on the default
  vault only unless you narrow with `--vault`.
- **Maintenance sweeps are sectioned.** `lint`, `doctor` (diagnostics), and
  `index` visit every vault with a labeled section per vault; a vault that
  cannot support an operation is skipped with a printed notice, and skips
  never change the exit code. `lint --strict` exits nonzero on a TAG finding
  in any vault.

### Per-project vaults

A repo can carry its own `.agent-wiki/config.yaml` declaring extra vaults
(and optionally its own `default_vault`). awiki finds the nearest one walking
up from the current directory to `$HOME` and merges it additively over the
global config, the local file winning on name collision. A relative `path:`
in a local config resolves against the directory containing `.agent-wiki`,
so the config means the same vault from any subdirectory. Because a checked-in
config is repo-controlled content, it is honored only after you trust its
directory explicitly:

```bash
awiki vault trust ~/code/my-project
```

An untrusted local config is ignored with a one-line stderr notice naming the
file and the trust command - never silently. A vault absent from the merged
view is invisible to every command, even if its directory exists on disk.

### Serving

`awiki serve` serves exactly **one vault per instance** - the default vault
unless you pick one with `--vault NAME` - and `--port` overrides the
`server.yaml` port, so one config dir can serve several vaults from separate
processes on different ports. The HTTP wire contract stays vault-implicit
(no vault selector in the routes). A single multiplexed server that mounts
several vaults under one port is the planned evolution of this design.

## Network server

A vault can be served over HTTP so a remote AI assistant reads from and contributes
to the same vault using the **same `awiki` CLI** as a transparent client. The CLI
talks to a `VaultService` facade; locally that is an in-process call, remotely it is
an HTTP round-trip - every command behaves the same either way.

### Server host

The HTTP server ships in the default install - no extra to install. Add a token
and run the daemon:

```bash
awiki token add laptop --role admin   # prints the secret ONCE; only its hash is stored
awiki serve                           # binds 127.0.0.1:8731 by default
```

Tokens live in `~/.config/agent-wiki/server.yaml` (respects
`AGENT_WIKI_CONFIG_DIR`), **never** in the vault. Manage them with:

```bash
awiki token add <name> --role reader|writer|admin
awiki token list      # names + roles only, never secrets
awiki token revoke <name>
```

Roles are ranked `reader < writer < admin`: readers can search/show/status/log/lint/context,
writers can additionally ingest/index/sync/adapt, and `doctor` requires admin.

### Running as a service (systemd)

A sample unit ships at
[`agent-wiki.service`](https://github.com/TacoTakumi/agent-wiki/blob/main/agent-wiki.service):

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
installed `awiki` binary - run `which awiki` to find it (inside a uv venv it lives
in `.venv/bin/awiki`, not `/usr/local/bin`). Keep `--bind`/`--port` consistent with
how you terminate TLS in front of it (see below).

### TLS

`awiki serve` speaks plain HTTP and binds to loopback by default. For remote access,
terminate TLS at a reverse proxy (nginx/Caddy) in front of it and point clients at the
HTTPS URL - the server itself does not manage certificates.

### Remote client

On another machine, point the CLI at the server:

```bash
awiki init --remote https://wiki.example.com --token <secret>
awiki search "raft consensus"   # transparently runs over HTTP
awiki init --clear              # drop the remote config from this client
```

Local and remote vaults are mutually exclusive: setting one clears the other.

### Switching between local and remote

Switching only rewrites the client config at `~/.config/agent-wiki/config.yaml` -
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
# ...later, to go back to the local vault:
cp ~/.config/agent-wiki/config.yaml.local-bak ~/.config/agent-wiki/config.yaml
```

Your local vault files are never destroyed by either direction of the switch - you
can also ingest them into the remote later with
`awiki ingest <path-to-old-vault>/...` (plain `ingest` uploads from the client;
`sync`/`adapt` are server-side - see below).

### Server-host semantics

A few commands act on the **server's** machine, not the client's:

- `sync` and `adapt` read the *server's* configured session directories and take *server-side* paths/refs - they do not see files on the client.
- `hook` is always local (it wires the client's agent CLI; there is no server hook).
- Mutating operations serialize via per-vault file locks; under contention a request may return `503` (the CLI surfaces this as "server busy, try again").

## Roadmap

These are planned but not yet implemented:

- **Smart ingestion** - use `claude -p` or a local model to extract entities, generate cross-references, and decide which existing pages to update during ingest
- **Shared wikis** - sync topics between personal and team wikis, with per-topic or per-user read/write permissions
- **Additional agent support** - AGENTS.md and wrappers for Cursor, Codex, and other agents
- **Research agent** - a dedicated agent that combines wiki search with web search, filing results back into the wiki automatically
- **Auto-capture** - agent notices high-value findings during conversations and suggests saving them (with user confirmation)
- **Vault viewer** - lightweight web UI for browsing the vault without Obsidian

## Install from source

From a checkout, with [uv](https://docs.astral.sh/uv/) installed
(`curl -LsSf https://astral.sh/uv/install.sh | sh`):

```bash
uv tool install .              # install `awiki` globally, from the repo root
uv tool install --reinstall .  # update after pulling changes
uv tool uninstall agent-wiki-kb
```

`pipx install .` and `pip install .` work the same way if you prefer them.

To build distributables instead, `uv build` writes the wheel and sdist into
`dist/`; install the wheel anywhere with
`uv tool install ./dist/agent_wiki_kb-<version>-py3-none-any.whl` (no source
checkout needed).

### Optional extras

- **ripgrep** (`rg`) - search uses ripgrep when available, and falls back to a built-in Python scan.
- **pdfplumber** - a permissive-licensed PDF extractor as an alternative to the default; `pip install 'agent-wiki-kb[pdf-permissive]'` and set `pdf_extractor: pdfplumber` in `wiki.yaml`.

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
uv pip install pytest        # tests are not in the dependencies
python -m pytest -v
```

### Project structure

```
agent-wiki/
  src/agent_wiki/
    cli.py            # Click CLI with all commands
    config.py         # user and vault config loading
    vault.py          # vault initialization
    ingest.py         # ingestion to raw/ + wiki page creation
    fetch.py          # URL fetch + extraction (HTML, PDF)
    search.py         # full-text search (ripgrep + Python fallback)
    show.py           # print a vault file by vault-relative path
    index.py          # rebuild index.md
    lint.py           # audit links, orphans, drift, frontmatter, tags
    page.py           # page model: frontmatter, slugify, wikilinks
    tags.py           # tag canonicalization engine
    tag_yaml.py       # comment-preserving wiki.yaml tags writer
    tag_fix.py        # whole-vault tag canonicalization
    tag_suggest.py    # vocabulary drafting from tags in use
    context.py        # auto-context hook: keyword extract, search, format
    guide.py          # the `awiki guide` block
    doctor.py         # schema drift inspection and repair
    conversation.py   # conversation bundle model
    sync.py           # adapter-driven conversation sync
    summarize.py      # optional LLM summarization backends
    service.py        # VaultService facade (local or remote)
    remote.py         # HTTP client for a served vault
    server/           # HTTP server
    server_config.py  # server tokens and roles
    locking.py        # per-vault file locks
    log.py            # append-only activity log
    redact.py         # secret redaction on ingest
    adapters/         # claude_code, opencode, pi, drop_zone
    hooks/            # per-agent install backends (claude, pi, opencode, manual; managed_file shared)
    data/guide.md     # the canonical memory-file block
    skills/           # bundled agent skills (package data)
  tests/              # pytest suite
  Doc/                # design notes and schemas
```

Design notes and schemas live in
[`Doc/`](https://github.com/TacoTakumi/agent-wiki/tree/main/Doc); the release
history is in
[CHANGELOG.md](https://github.com/TacoTakumi/agent-wiki/blob/main/CHANGELOG.md).

## License

[GPL-3.0-or-later](https://github.com/TacoTakumi/agent-wiki/blob/main/LICENSE).
