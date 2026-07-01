---
name: awiki-save
description: "Save content from the current conversation to the Agent Wiki. Use when research, decisions, or valuable findings should be preserved."
---

# Agent Wiki Save

Save valuable content from the current conversation to the wiki. First decide
whether you're creating a **new** page or **updating an existing** one — search
first (`awiki search "<query>"`) so you don't create a duplicate.

## New page

1. **Generate a summary**: Write a clean markdown summary of the content to save. Include:
   - A clear `# Title` heading
   - Key findings, decisions, or knowledge
   - `[[wikilinks]]` to related topics if known

2. **Write to a temp file**: Save the summary to a temp `.md` file

3. **Ingest**: Run `awiki ingest <temp-file> --topic <topic> --tags <tags>`

4. **Report**: Tell the user what was saved and where

## Updating an existing page

Pages are **generated from an immutable-by-convention source in `raw/`** — never
hand-edit a page in a topic folder. To fold a finding into an existing page:

1. **Find the raw source**: The page's raw is `raw/<stem>.md`, where `<stem>` is
   the page's filename without the topic folder or `.md` (the page's `sources:`
   frontmatter also lists it). Run `awiki status` if you need the vault path.

2. **Edit the raw** (`raw/<stem>.md`), not the page.

3. **Reingest**: Run `awiki reingest <stem>` — note it takes the bare **stem**
   (`my-page`), not a vault-relative path like `projects/my-page.md`. This
   re-renders the page from the raw; it does not author changes itself.

4. **Review the diff**: On a normal edit-the-raw loop, reingest will stop with a
   divergence diff (the page's pre-edit lines differ from your edited raw). That
   is expected — confirm the `-` lines are just the old versions of what you
   rewrote (not unique page-only content you'd lose), then re-run with `--force`.

5. **Report** what changed.

> **Footgun:** `reingest` regenerates the page's slug from the first `# H1`.
> Changing the H1 renames the page path and orphans the old one — keep the H1
> stable unless you intend to rename.
>
> Alternative: `awiki ingest <file> --update` authors the raw externally and
> rebuilds, matched by basename. Use one path or the other, not both at once.

> **Remote vault** (`awiki init --remote …`): the raw lives on the *server*, so
> steps 1–2 (editing `raw/<stem>.md` in place) don't apply — over the wire
> `reingest` only re-renders the page from the server's *current* raw. To change
> content remotely: `awiki show raw/<stem>.md > <stem>.md`, edit it, then
> `awiki ingest <stem>.md --update` (the client uploads the new bytes; the server
> rewrites the matching raw and rebuilds).

## Choosing a Topic

Ask the user which topic to file under if not obvious:
- `projects` — project-specific knowledge
- `decisions` — architectural or technical decisions
- `research` — general research findings
- `tools` — tool configurations and usage notes

## Choosing Tags

Suggest relevant tags based on the content. Keep them short and lowercase.
