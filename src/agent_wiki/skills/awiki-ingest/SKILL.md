---
name: awiki-ingest
description: "Ingest an existing file into the Agent Wiki. Use when the user has a file they want to add to the knowledge base."
---

# Agent Wiki Ingest

Ingest an existing markdown file into the wiki vault.

## Usage

1. Confirm the file path with the user
2. Optionally ask for topic and tags
3. Run: `awiki ingest <path> --topic <topic> --tags <tags>`
4. Report what was ingested and where the wiki page was created

## Updating an existing page

Pages are generated from `raw/`, so update the source, not the page:

1. Edit the page's `raw/<name>` file.
2. Run `awiki reingest <name>`.
3. If it reports the page has diverged from its raw, it prints a diff and stops.
   Review it: fold anything worth keeping into the raw, then re-run
   `awiki reingest <name> --force` to rebuild from raw.

To update from an *external* file (not the vault's own raw), use
`awiki ingest --update <path>` (add `--force` if the page has diverged).
