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

> Works the same against a local or remote vault. When connected to a remote
> server (`awiki init --remote`), the file's contents are uploaded to the
> server — `<path>` is read on this machine, so a local path is correct.
