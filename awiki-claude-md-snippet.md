## Knowledge base: the Agent Wiki (awiki)

This project's durable knowledge — decisions, research, tool/config
details, and hard-won findings — lives in the **Agent Wiki**, a local
markdown vault queried through the `awiki` CLI. Prefer it over
re-deriving facts or searching the web.

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

If `awiki` isn't installed or no vault is configured, skip the wiki and
proceed normally — don't block on it.
