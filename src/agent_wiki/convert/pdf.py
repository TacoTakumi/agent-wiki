"""PDF converter: one local PDF to a ``ConvertedDocument``.

The outline (bookmarks) becomes a ``TocEntry`` list with 1-based pages and goes
through the shared splitter. Each planned chapter covers the pages from its
start page up to the next chapter's start page; pages before the first chapter
belong to chapter 1. The text of each page range comes from the configured PDF
extractor in ``fetch`` (pymupdf4llm by default, pdfplumber when configured).
A PDF without an outline is one chapter.

The title is the metadata title, else the first heading (the first text set
larger than the body text), else the file stem.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pymupdf

from agent_wiki.convert.model import Chapter, ConvertedDocument, TocEntry
from agent_wiki.convert.split import plan_chapters
from agent_wiki.fetch import pdf_to_markdown

# Pages scanned for the first heading; enough for a title page and front matter.
_HEADING_SCAN_PAGES = 5


def convert_pdf(path: str | Path, *,
                extractor: str = "pymupdf4llm") -> ConvertedDocument:
    """Convert the PDF at ``path`` into chapters.

    ``extractor`` is the vault's ``pdf_extractor`` setting. An unreadable file
    or an unknown extractor raises ``ValueError``.
    """
    path = Path(path)
    data = path.read_bytes()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # not a PDF, or damaged beyond opening
        raise ValueError(f"not a readable PDF: {e}") from e
    with doc:
        n_pages = doc.page_count
        toc = [TocEntry(title.strip(), level, page=page)
               for level, title, page, *_ in doc.get_toc()
               if 1 <= page <= n_pages and title.strip()]
        heading = _first_heading(doc)
        meta = doc.metadata or {}

    plan = plan_chapters(toc, [] if toc else [path.name],
                         first_heading=lambda _item: heading)
    starts = [1] + [p.start.page for p in plan[1:]]
    ends = starts[1:] + [n_pages + 1]
    chapters = [
        Chapter(p.ordinal, p.title,
                pdf_to_markdown(data, extractor,
                                pages=range(start - 1, end - 1)).strip() + "\n")
        for p, start, end in zip(plan, starts, ends)
    ]
    return ConvertedDocument(
        title=(meta.get("title") or "").strip() or heading or path.stem,
        chapters=chapters,
        author=(meta.get("author") or "").strip() or None,
    )


def _first_heading(doc: pymupdf.Document) -> str | None:
    """The first line set larger than the body text in the first pages."""
    lines: list[tuple[float, str]] = []
    for page in doc.pages(0, min(doc.page_count, _HEADING_SCAN_PAGES)):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if text:
                    size = max(s["size"] for s in line["spans"])
                    lines.append((round(size, 1), text))
    if not lines:
        return None
    sizes: Counter[float] = Counter()
    for size, text in lines:
        sizes[size] += len(text)
    body = sizes.most_common(1)[0][0]
    return next((text for size, text in lines if size > body), None)
