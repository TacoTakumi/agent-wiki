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

**Pages are generated; edit the raw, not the page.** Each page is rendered from an
immutable file in `raw/`. To change a page, edit its `raw/<name>` source, then
`awiki reingest <name>`. If the page has diverged from its raw, reingest shows a diff
and stops — review it, fold anything worth keeping into the raw, then re-run with
`--force`. Never hand-edit a page in a topic folder.

If `awiki` isn't installed or no vault is configured, skip the wiki and
proceed normally — don't block on it.
