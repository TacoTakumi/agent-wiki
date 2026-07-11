# Changelog

All notable changes to Agent Wiki are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of truth for the version is `__version__` in
`src/agent_wiki/__init__.py`; `awiki --version` and the build metadata both
derive from it. Release tags begin at `v0.5.0`; earlier versions and dates
below are reconstructed from the commits that bumped `__version__`.

## [0.7.0]

### Added
- **`awiki skills` command group.** A new subcommand group installs the three
  bundled agent skills (`awiki-search`, `awiki-save`, `awiki-ingest`) into
  whatever agent harness is detected (Claude Code, pi, Hermes, opencode),
  backed by the AgentSquire library. `awiki skills install` copies them in with
  a provenance stamp; `status`, `update`, and `uninstall` manage them;
  `--scope user|project` selects the target and `--harness NAME` narrows to one
  harness. The skills now ship inside the wheel as package data under
  `src/agent_wiki/skills/`, so no source checkout is needed to install them.
- **Proactive skill-staleness notice.** Every `awiki` invocation runs a safe
  startup check: when your installed skills are older than the bundled copies
  it prints one stderr line naming `awiki skills update`, without ever
  prompting, reading stdin, or touching stdout or the exit code. Agents see the
  notice too - it is not gated on an interactive terminal - and `CI` or
  `AGENTSQUIRE_NO_UPDATE_CHECK` suppress it.

### Changed
- **`agentsquire` now resolves from PyPI.** Now that `agentsquire` is published
  to PyPI, the dev-time `[tool.uv.sources]` editable override that pinned it to
  the sibling `../AgentSquire` checkout has been removed from `pyproject.toml`.
  A fresh install or `uv sync` pulls `agentsquire>=0.2.1` (currently 0.3.0)
  straight from the index - no side-by-side source checkout required.

### Migration
- If you previously installed the skills by hand-copying **or symlinking** the
  old repo-root `skills/` directory into a harness (e.g. `~/.claude/skills/`),
  run `awiki skills update --force` once to adopt them under provenance. Both
  unstamped copies and symlinks are classified as locally modified and are never
  silently overwritten, so a plain `install`/`update` skips them with a reason
  (it will not crash on a pre-existing symlink); `--force` replaces them with a
  stamped copy (a symlink is removed, not followed). Alternatively, delete the
  old skill directories or symlinks and run `awiki skills install`. Note that
  this release deleted the old repo-root `skills/` tree, so any symlink pointing
  into it is now dangling - `--force` it or remove it.

## [0.6.0]

### Changed
- **`awiki guide` output is trimmed and de-versioned.** The self-installing
  block is now a static, self-contained ~15-line block: it frames the wiki as
  the first stop for durable project/domain knowledge (no longer pitched as a
  general web-search replacement), keeps the search→show and edit-raw→reingest
  habits inline, and carries the `awiki-save` nudge. The default `awiki guide`
  preamble is a short "add this once; leave it if already present" instruction.

### Removed
- **BREAKING: the `<!-- awiki:begin vX.Y.Z -->` / `<!-- awiki:end -->` markers
  and the version-staleness note are gone** from the `awiki guide` output. The
  installed block is now static, so consumers add it once and re-adapt only when
  they choose to — there is no marker to detect staleness or re-sync against.
  Pre-1.0 this rides a minor bump, but it changes the `awiki guide` artifact
  format, so it is called out explicitly.

## [0.5.0] – 2026-07-08

### Changed
- **Renamed `awiki directions` → `awiki guide`.** The command that prints the
  self-installing agent-onboarding block now reads `awiki guide`, which reads
  naturally as a "point your agent here and run this" call to action (and now
  headlines the README's get-started line). The old name still works as a
  **hidden, deprecated alias**, so existing muscle memory and any half-installed
  memory-file blocks keep functioning. The `<!-- awiki:begin vX.Y.Z -->` marker
  is unchanged, so installed blocks still detect staleness and re-sync normally.

## [0.4.0] – 2026-07-06

### Added
- **`render_hash` drift guard.** Every rendered page now carries a `render_hash`
  fingerprint of its body in frontmatter, letting awiki tell an intended,
  raw-driven update apart from an out-of-band hand-edit of the page:
  - Editing `raw/<name>` and running `awiki reingest <name>` rebuilds the page
    cleanly — **a raw edit no longer trips the guard**, so the canonical
    edit-the-raw loop needs no `--force`.
  - The guard now fires only when the *page itself* was hand-edited out of band;
    `reingest` (and `ingest --update`) then print a page-vs-raw diff and stop
    until you review and re-run with `--force`.
  - Lazy trust-on-first-use: pre-existing pages without a `render_hash` are
    trusted the first time they're touched and stamped going forward, so
    upgrading an existing vault needs no migration step.
- **`awiki raw <name>`** — resolve a page to its `raw/<name>` source path,
  printed to stdout so it drops straight into command substitution
  (`$EDITOR "$(awiki raw my-notes.md)"`). Errors exactly as `reingest` does on a
  missing or ambiguous name; on a remote vault it prints the server-side
  reference and notes on stderr that the raw isn't locally editable.
- **`awiki doctor` render-hash checks** — stamps `render_hash` on un-hashed but
  faithful pages, and reports un-hashed pages whose body has diverged from their
  `raw/` source.

### Changed
- `awiki reingest` and `awiki show` now print the resolved read/write location
  (a local absolute path, or the server URL + vault-relative path for a remote
  vault) on **stderr**. stdout stays byte-identical, so skills that parse command
  output verbatim are unaffected.

## [0.3.1] – 2026-07-01

### Fixed
- `awiki directions`: corrected the raw-editing guidance so the installed block
  tells agents to edit the `raw/` source and run `awiki reingest`, never
  hand-edit a rendered page.
- Documented the page-update path — edit raw → `reingest`, or `ingest --update`
  for an external file — in the directions block and the `awiki-save` skill,
  including the remote-vault case.

## [0.3.0] – 2026-06-27

### Added
- **Tag vocabulary system.** An optional `tags:` block in `wiki.yaml`
  (`mode: off | warn | strict` plus a preferred → aliases map) canonicalizes
  tags across the vault:
  - `awiki tag add <preferred> [--alias …]` — persist vocabulary entries through
    a comment-preserving `wiki.yaml` writer (idempotent; refuses to steal an
    alias already bound to another term).
  - `awiki tag suggest [--write]` — draft a vocabulary from the tags already in
    use, grouping related tags as alias candidates.
  - `awiki tag fix [--write] [--topic T] [PATH]` — canonicalize existing pages'
    frontmatter tags (preview by default; page frontmatter only, never `raw/` or
    the page body).
  - Ingest canonicalizes tags at the single write boundary; `--tag-mode
    off|warn|strict` overrides the mode per ingest, and `strict` rejects
    out-of-vocabulary tags pre-flight.
  - `awiki lint` gained a read-only `TAG` (tag-audit) check; `lint --strict`
    turns it into a CI gate (non-zero exit on any TAG finding).

### Fixed
- Bare `mode: off` tag blocks round-trip safely through the `wiki.yaml` writer.
- `canonicalize_tags` hardened against non-string tag values.

## [0.2.1] – 2026-06-26

### Added
- **`--vault PATH` / `AWIKI_VAULT`** — override the configured vault for a single
  invocation (forces a local vault).
- `awiki doctor` now repairs a stale local `vault_path` (config pointing at a
  vault that no longer exists) instead of hard-stopping.

## [0.2.0] – 2026-06-25

### Added
- **URL ingest across the network server.** Remote clients fetch page content
  locally, so `awiki ingest <url>` works whether the vault is local or served
  over HTTP.
- New `awiki lint` checks:
  - `SOURCE` — a `raw/` file edited in place (drifted from its recorded sha256).
  - `STALE` — a page whose body lags its newest source.
  - `SIZE` — pages over 200 lines, flagged as split candidates.
  - `INDEX` — pages missing from `index.md`.
  - `lint --refetch` — re-fetch URL sources and flag any whose upstream content
    changed (`UPSTREAM`; network, off by default, local vaults only).

### Fixed
- `awiki doctor --reconcile-raw` refreshes the provenance sidecar's sha256.

## [0.1.1] – 2026-06-20

### Added
- **URL ingestion.** `awiki ingest <url>` fetches and ingests web pages:
  - HTML extracted via trafilatura; PDFs via pymupdf4llm (pdfplumber selectable).
  - Every ingest writes a sha256 **provenance sidecar** (`raw/<name>.meta.yaml`);
    the original fetched artifact is archived byte-identically under `raw/assets/`.
  - Normalized-URL dedup and a sha256 skip avoid re-ingesting unchanged URLs.
  - `source_url` is emitted inline on fetched pages; non-text content types are
    rejected with a friendly one-line error.
- Single-source-of-truth versioning: everything derives from `__version__`, and
  `awiki directions` re-adapts an installed block when a newer version ships
  (shared-vault framing).

### Changed
- Licensed under **GPL-3.0-or-later**.

### Fixed
- `awiki lint` no longer flags provenance sidecars as un-ingested `raw/` files.

## [0.1.0] – 2026-04-14

Initial release.

### Added
- **Core vault + CLI** (`awiki` / `aw`): `init`, `ingest` (files), `search`,
  `show`, `index`, `lint`, `status`, `log` over a plain-markdown vault with YAML
  frontmatter and `[[wikilinks]]`.
- **Multi-word search** — AND-across-the-page matching with coverage-ranked
  results and a lower-ranked partial-match tier.
- **`awiki show <path>`** — print any vault file verbatim by its vault-relative
  path.
- **`ingest --update`** plus a collision guard that refuses to clobber an
  existing `raw/` basename (per-file skip/continue in globs).
- **`awiki reingest <name>`** — rebuild a page from its edited `raw/<name>`
  source (diff-and-stop unless `--force`); the canonical page-edit loop.
- **Conversation ingest** — adapters for Claude Code, OpenCode, and a drop-zone,
  a canonical Conversation Bundle format, `awiki sync` (state-tracked,
  idempotent), `awiki adapt`, `awiki ingest-conversation`, and optional
  summarization (`none` / `claude-p` / `local-openai`).
- **Auto-context hook** — `awiki context` (YAKE keyword extraction → search →
  compact pointer block) and `awiki hook install|uninstall|status` to wire it
  into an agent CLI's `UserPromptSubmit` hook.
- **`awiki directions`** — a self-installing wiki-usage block for agent memory
  files (`CLAUDE.md`, `AGENTS.md`, …).
- **Network server** — `awiki serve` (FastAPI, bearer auth, role-gated
  reader/writer/admin), `awiki token add|list|revoke`, a transparent remote
  client (`awiki init --remote … --token …`), per-vault file locks, and
  `awiki doctor` for schema-drift repair.
- **Claude Code skills** — `awiki-search`, `awiki-save`, `awiki-ingest`.
