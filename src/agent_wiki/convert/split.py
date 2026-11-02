"""Format-neutral chapter splitter.

``plan_chapters`` turns a table of contents (a ``TocEntry`` list in document
order) and a spine (the ordered content items) into a chapter plan. It never
reads content: a converter cuts its own content between the ``start`` anchors
of consecutive planned chapters, and supplies callbacks for the two facts that
need content (the text between a parent and its child anchor, and the first
heading of a spine item).

Rules:

- The chapter level is the shallowest TOC depth with at least 3 entries (the
  shallowest depth present when none has 3). Every entry at that level starts
  a chapter.
- Deeper entries do not start chapters; they are listed in ``merged`` of the
  chapter they follow. Shallower entries (part titles above the chapters) are
  dropped from the plan; their content falls into the chapter before them.
- A chapter entry with exactly one direct child, and no paragraph text between
  the two anchors, gets the title ``'<Parent>: <Child>'``, with a parent label
  such as ``'CHAPTER: 1'`` normalised to ``'Chapter 1'`` first.
- With no TOC, or a TOC that points into fewer than half of the spine items,
  each spine item is one chapter, titled from its first heading or
  ``'Part NN'``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from agent_wiki.convert.model import TocEntry

_ROMAN = r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_LABEL = re.compile(
    rf"^\W*(chapter|part|book|section)\W*(\d+|{_ROMAN})\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class PlannedChapter:
    """One chapter of the plan.

    ``start`` is the entry whose anchor opens the chapter; the chapter runs to
    the next chapter's ``start``. In the spine fallback it is a synthetic
    level-1 entry whose ``target`` is the spine item. ``merged`` holds the
    deeper TOC entries whose content stays inside this chapter.
    """

    ordinal: int
    title: str
    start: TocEntry
    merged: tuple[TocEntry, ...] = ()


def format_ordinal(ordinal: int, total: int) -> str:
    """Zero-pad ``ordinal`` to the digit count of ``total``, at least 2."""
    return str(ordinal).zfill(max(2, len(str(total))))


def normalise_label(title: str) -> str:
    """Normalise a 'chapter/part/book/section + number' label to 'Chapter 1'.

    Any case and punctuation is accepted; the number may be arabic or roman
    (roman is upper-cased). Other titles come back unchanged.
    """
    m = _LABEL.match(title)
    if not m or not m.group(2):
        return title
    number = m.group(2)
    return f"{m.group(1).title()} {number if number.isdigit() else number.upper()}"


def plan_chapters(
    toc: Sequence[TocEntry],
    spine: Sequence[str],
    *,
    first_heading: Callable[[str], str | None] | None = None,
    has_text_between: Callable[[TocEntry, TocEntry], bool] | None = None,
) -> list[PlannedChapter]:
    """Return the chapter plan for a document.

    ``spine`` lists the content items in reading order (EPUB member paths).
    When TOC targets are not spine items (HTML fragment ids, PDF pages), pass
    an empty spine with a non-empty TOC to skip the coverage check, and a
    one-item spine with an empty TOC to get a single chapter.

    ``first_heading(item)`` returns the first heading text of a spine item or
    None; it is used only by the spine fallback (without it every fallback
    chapter is titled 'Part NN').

    ``has_text_between(parent, child)`` says whether paragraph text sits
    between a chapter entry and its lone child. Without it no titles are
    joined.
    """
    if not toc or not _covers(toc, spine):
        return _from_spine(spine, first_heading)
    return _from_toc(list(toc), has_text_between)


def _covers(toc: Sequence[TocEntry], spine: Sequence[str]) -> bool:
    if not spine:
        return True
    items = set(spine)
    hit = {e.target.split("#", 1)[0] for e in toc} & items
    return len(hit) * 2 >= len(items)


def _chapter_level(toc: list[TocEntry]) -> int:
    counts: dict[int, int] = {}
    for e in toc:
        counts[e.level] = counts.get(e.level, 0) + 1
    rich = [level for level, n in counts.items() if n >= 3]
    return min(rich) if rich else min(counts)


def _from_toc(toc: list[TocEntry],
              has_text_between: Callable[[TocEntry, TocEntry], bool] | None,
              ) -> list[PlannedChapter]:
    level = _chapter_level(toc)
    groups: list[tuple[TocEntry, list[TocEntry]]] = []
    leading: list[TocEntry] = []
    for e in toc:
        if e.level == level:
            groups.append((e, []))
        elif e.level > level:
            (groups[-1][1] if groups else leading).append(e)
    if groups:
        groups[0][1][:0] = leading

    plan = []
    for ordinal, (start, merged) in enumerate(groups, 1):
        title = start.title
        children = [e for e in merged if e.level == level + 1]
        if (len(children) == 1 and has_text_between is not None
                and not has_text_between(start, children[0])):
            title = f"{normalise_label(start.title)}: {children[0].title}"
        plan.append(PlannedChapter(ordinal, title, start, tuple(merged)))
    return plan


def _from_spine(spine: Sequence[str],
                first_heading: Callable[[str], str | None] | None,
                ) -> list[PlannedChapter]:
    plan = []
    for ordinal, item in enumerate(spine, 1):
        heading = first_heading(item) if first_heading else None
        title = heading or f"Part {format_ordinal(ordinal, len(spine))}"
        plan.append(PlannedChapter(ordinal, title, TocEntry(title, 1, item)))
    return plan
