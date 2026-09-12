---
name: awiki-search
description: "Search the Agent Wiki knowledge base. Use before web searching for technical knowledge."
---

# Agent Wiki Search

Search the wiki knowledge base for existing knowledge before resorting to web searches.

## Usage

1. Run `awiki search "<query>"` to search the wiki
2. Optionally filter by topic: `awiki search "<query>" --topic <topic>`
3. Present results to the user
4. If no results found, inform the user and suggest a web search

## Reading a page

Search prints only matching snippets, not whole pages. To read one, run
`awiki show <path>` with the vault-relative path printed in the results:

    awiki show research/raft-consensus.md

That is right for most pages. A long one - a log, a watch list, a page whose
outline runs past a screen - is better read in parts:

- `--outline` prints the page's heading lines only, so you can pick a target
- `--section "<heading text>"` prints the first section whose heading contains
  that text, subsections included
- `--head N` / `--tail N` keep the first or last N *child* sections of that
  section - each kept child brings its own deeper subsections with it - or the
  first or last N top-level sections when no `--section` is given

For example:

    awiki show research/raft-consensus.md --outline
    awiki show research/raft-consensus.md --section "Leader election"
    awiki show projects/deploy-log.md --tail 3

Any of these prints plain markdown without the YAML frontmatter; a plain
`awiki show` prints the page verbatim.

## When to Use

- Before any web search for technical knowledge
- When the user asks about a topic that may have been previously researched
- When looking for project decisions, tool configurations, or research notes
