---
name: awiki-save
description: "Save content from the current conversation to the Agent Wiki. Use when research, decisions, or valuable findings should be preserved."
---

# Agent Wiki Save

Save valuable content from the current conversation to the wiki as a two-step process.

## Process

1. **Generate a summary**: Write a clean markdown summary of the content to save. Include:
   - A clear `# Title` heading
   - Key findings, decisions, or knowledge
   - `[[wikilinks]]` to related topics if known

2. **Write to a temp file**: Save the summary to a temp `.md` file

3. **Ingest**: Run `awiki ingest <temp-file> --topic <topic> --tags <tags>`

4. **Report**: Tell the user what was saved and where

> Works the same against a local or remote vault. The temp file is created and
> read on this machine; when connected to a remote server its contents are
> uploaded, so a local temp path is correct.

## Choosing a Topic

Ask the user which topic to file under if not obvious:
- `projects` — project-specific knowledge
- `decisions` — architectural or technical decisions
- `research` — general research findings
- `tools` — tool configurations and usage notes

## Choosing Tags

Suggest relevant tags based on the content. Keep them short and lowercase.
