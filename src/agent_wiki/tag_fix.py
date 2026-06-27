"""'awiki tag fix' engine (REQ-15, REQ-16).

Whole-vault bulk cleanup of existing pages (D-08): traverse every topic-folder
page (raw/, index.md, log.md are excluded by construction — only topic dirs are
scanned), run each page's frontmatter tags through canonicalize_tags, and collect
the pages that would change or carry a novel tag. The collection is read-only so
preview can report it without writing; apply_tag_fix performs the frontmatter-only
rewrite. Scope is bounded to known-alias canonicalization: novel out-of-vocabulary
tags are reported but never auto-changed (a human promotes via 'tag add' or drops
them)."""

from dataclasses import dataclass, field
from pathlib import Path

from agent_wiki.config import TagVocabulary
from agent_wiki.page import parse_page, update_frontmatter
from agent_wiki.tags import canonicalize_tags


@dataclass(frozen=True)
class PageTagFix:
    """One page surfaced by `tag fix`: either its tags would canonicalize, or it
    carries a novel tag (or both).

    `path` is vault-relative. `before`/`after` are the on-disk and canonical tag
    lists. `remaps` are the (alias, preferred) rewrites; `novel` the out-of-vocab
    tags left unchanged. `changed` is True only when --write would rewrite the
    page (the canonical list differs from disk)."""

    path: Path
    before: list = field(default_factory=list)
    after: list = field(default_factory=list)
    remaps: list = field(default_factory=list)
    novel: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.after != self.before


def _within(md_file: Path, root: Path) -> bool:
    """True if `md_file` is `root` itself (a file) or lies under it (a directory)."""
    mf = md_file.resolve()
    return mf == root or root in mf.parents


def collect_tag_fixes(vault_path: Path, vocab: TagVocabulary,
                      topics: list, *, root: Path | None = None) -> list:
    """Scan `topics` under `vault_path` for pages `tag fix` should surface.

    Returns a PageTagFix per topic-folder page whose frontmatter tags would
    canonicalize (alias remap, casing, or de-dup) or that carries a novel tag.
    Read-only — never writes. `root` (a resolved file or directory) narrows the
    pass to that subtree; raw/, index.md and log.md stay excluded regardless,
    since only topic dirs are walked. With an off/empty vocabulary the result is
    empty, so `tag fix` is inert on non-vocabulary vaults."""
    fixes: list = []
    if vocab.is_off:
        return fixes
    for topic in topics:
        topic_dir = vault_path / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.rglob("*.md")):
            if root is not None and not _within(md_file, root):
                continue
            before = list(parse_page(md_file)["meta"].get("tags") or [])
            if not before:
                continue
            result = canonicalize_tags(before, vocab)
            if result.tags != before or result.novel:
                fixes.append(PageTagFix(
                    path=md_file.relative_to(vault_path),
                    before=before,
                    after=result.tags,
                    remaps=result.remaps,
                    novel=result.novel,
                ))
    return fixes


def apply_tag_fix(vault_path: Path, fix: PageTagFix) -> bool:
    """Apply one fix: rewrite the page's frontmatter tag list to its canonical form.

    Frontmatter-only — the page body stays byte-identical and raw/ is never
    touched. A no-op (returns False) when the page would not change (e.g. a
    novel-only finding, which carries no canonicalization to apply)."""
    if not fix.changed:
        return False
    page_path = vault_path / fix.path
    meta = parse_page(page_path)["meta"] or {}
    meta["tags"] = fix.after
    update_frontmatter(page_path, meta)
    return True
