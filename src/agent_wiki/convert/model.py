"""The format-neutral shape every document converter returns.

A converter (EPUB, HTML, PDF) reads one local file and produces a
``ConvertedDocument``: the document title and metadata, the cover member, and
the ordered chapters, each with its markdown and the image members it
references. The chapter splitter reads ``TocEntry`` lists, and the vault writer
reads only ``ConvertedDocument``, so neither needs to know the source format.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TocEntry:
    """One table-of-contents entry, in document order.

    ``level`` is 1 for a top-level entry, 2 for its children and so on.
    ``target`` is the place the entry points at for markup formats: an archive
    member path with an optional fragment (``OEBPS/ch01.xhtml#c1``) or a bare
    fragment id for a single HTML file. ``page`` is the 1-based page for a PDF
    outline entry. A converter fills whichever of the two its format has.
    """

    title: str
    level: int
    target: str = ""
    page: int | None = None


@dataclass
class Chapter:
    """One chapter of a converted document.

    ``ordinal`` counts from 1 in reading order. ``markdown`` is the chapter
    body. ``images`` lists the archive member paths of the images the body
    references, in first-reference order and without duplicates.
    """

    ordinal: int
    title: str
    markdown: str
    images: list[str] = field(default_factory=list)


@dataclass
class ConvertedDocument:
    """A whole converted document, ready for the vault writer.

    ``author``, ``language`` and ``publisher`` are None when the format does
    not provide them, so the writer adds only the fields that exist.
    ``cover`` is the archive member path of the declared cover image, or None.
    ``assets`` maps each member path named by ``cover`` or by a chapter's
    ``images`` to its bytes, so the writer can extract them without reopening
    the source file.
    """

    title: str
    chapters: list[Chapter]
    author: str | None = None
    language: str | None = None
    publisher: str | None = None
    cover: str | None = None
    assets: dict[str, bytes] = field(default_factory=dict)

    @property
    def metadata(self) -> dict[str, str]:
        """The metadata fields the format provided, in frontmatter order."""
        fields = {"author": self.author, "language": self.language,
                  "publisher": self.publisher}
        return {k: v for k, v in fields.items() if v}
