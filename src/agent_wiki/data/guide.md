## Knowledge base: the Agent Wiki (awiki)

Durable, hard-won knowledge — decisions, research, tool/config details, fixes — lives in the
**Agent Wiki**, one markdown vault **shared across all your projects** (and the assistant itself),
reached through the `awiki` CLI. Go there first for project- or domain-specific knowledge, and
before re-deriving something that was likely figured out before; what you save there is available
from every other project.

**Search, then read the whole page.** `awiki search "<query>"` lists matching pages (title, path,
snippets); always `awiki show <path>` to read a page in full before acting. Multi-word queries
match every term, so add words to narrow.

**Save what's worth keeping** — a decision, a non-obvious fix, a reusable pattern — with the
**`awiki-save`** skill (or **`awiki-ingest`** to pull an existing file into the vault).

**Never hand-edit a page** — each is rendered from a source in `raw/`. To change one, edit its
`raw/<name>` source (find it with `awiki raw <name>`), then run `awiki reingest <name>` to
re-render it; no `--force` needed when only the raw changed.

If `awiki` isn't installed or no vault is configured, skip the wiki and proceed normally.
