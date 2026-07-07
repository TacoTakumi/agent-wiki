## Knowledge base: the Agent Wiki (awiki)

Durable knowledge — decisions, research, tool/config details, and
hard-won findings — lives in the **Agent Wiki**, a single markdown vault
**shared across all your projects** (and the assistant itself), queried
through the `awiki` CLI. Prefer it over re-deriving facts or searching the
web. Because the vault is shared, what you save here is available from every
other project — and you can draw on theirs from here.

**Search first, then read the full page:**

```bash
awiki search "<query>"        # returns matching pages: title, vault-relative path, snippets
awiki show <path>             # prints a full page verbatim (use the path search prints)
```

`awiki search` shows only snippets. Always `awiki show <path>` to read the
whole page before acting on what you found. Multi-word queries match every
term across a page (most-relevant first), so add words to narrow.

**When to reach for it:**

- Before any web search for project- or domain-specific knowledge.
- Before re-deriving something that feels like it was figured out before.
- When you need a past decision, a tool/config detail, or research notes.

**Save what's worth keeping.** After a meaningful finding — a decision, a
result that changes a recommendation, a non-obvious fix, or a pattern
worth reusing — record it with the **`awiki-save`** skill so future
sessions inherit it. To pull an existing file into the wiki, use the
**`awiki-ingest`** skill.

**Pages are generated from raw; edit the raw, never the page.** Each page in a
topic folder is *rendered* from a source file in `raw/` — the raw file is the
source of truth. To change a page, edit its `raw/<name>` source (find its path
with `awiki raw <name>`), then run `awiki reingest <name>` to re-render the page
from it. Editing only the raw never trips the drift guard — `reingest` rebuilds
the page cleanly, no `--force` needed. (`reingest` only propagates the raw's
content into the page — it never authors the change itself.) `--force` is for the
other case only: if the *page itself* was hand-edited out of band, `reingest`
prints a page-vs-raw diff and stops — review it, fold anything worth keeping into
the raw, then re-run with `--force`. Never hand-edit a page in a topic folder.

If `awiki` isn't installed or no vault is configured, skip the wiki and
proceed normally — don't block on it.
