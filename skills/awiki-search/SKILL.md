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

## Reading a full page

Search prints only matching snippets, not whole pages. To read a full page,
run `awiki show <path>` with the vault-relative path printed in the results:

    awiki show research/raft-consensus.md

> `search` and `show` work the same against a local or remote vault — when
> connected to a remote server (`awiki init --remote`) they transparently run
> over HTTP against the server's vault.

## When to Use

- Before any web search for technical knowledge
- When the user asks about a topic that may have been previously researched
- When looking for project decisions, tool configurations, or research notes
