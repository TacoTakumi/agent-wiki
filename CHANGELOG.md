# Changelog

All notable changes to Agent Wiki are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of truth for the version is `__version__` in
`src/agent_wiki/__init__.py`; `awiki --version` and the `awiki directions`
marker both derive from it. There are no release tags yet — the versions and
dates below are reconstructed from the commits that bumped `__version__`.

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
