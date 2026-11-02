"""Local HTML file to ``ConvertedDocument``.

The whole ``<body>`` is converted: there is no boilerplate removal, so nav,
header and footer text stays in the output. Every non-empty h1-h6 heading is
one table-of-contents entry at its heading level, anchored at the heading
element, and the shared splitter plans the chapters. When no heading level
has at least 3 headings the file is one chapter. Each chapter runs from its
heading to the next chapter's heading; the content before the first chapter
heading (nav, a page title, an introduction) goes to chapter 1. Every slice is
rendered through the shared markdown renderer.

The title is the ``<title>`` text, else the first heading, else the file stem.
``author`` comes from ``<meta name="author">`` and ``language`` from the
``lang`` attribute of ``<html>``.

An ``<img>`` whose ``src`` is a relative path to an image file inside the HTML
file's folder (subfolders included) is referenced by that normalised relative
path (``images/pic.png``) and its bytes go into ``assets``. Every other
``src`` (remote, ``data:``, absolute, climbing out of the folder, missing, not
an image) stays as written and is not read.
"""
from __future__ import annotations

import posixpath
import re
import secrets
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from agent_wiki.convert.markdown import html_to_markdown
from agent_wiki.convert.model import Chapter, ConvertedDocument, TocEntry
from agent_wiki.convert.split import plan_chapters

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
# A level needs this many headings before the file is split by it.
_MIN_CHAPTERS = 3


def convert_html(path: str | Path) -> ConvertedDocument:
    """Convert the HTML file at ``path`` into chapters.

    Raises ValueError with a one-line reason when the file cannot be read, is
    empty, holds binary data, or does not parse as HTML.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ValueError(f"could not read HTML file: {e}") from e
    if not data.strip():
        raise ValueError("HTML file is empty")
    if b"\x00" in data:
        raise ValueError("not an HTML file: it holds binary data")
    return _Document(_parse(data), path).convert()


def _parse(data: bytes):
    """The root element. UTF-8 is used when the bytes decode as UTF-8, else
    the parser follows the declared charset. Comments and processing
    instructions are dropped, so none can pose as a cut marker."""
    try:
        data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = None
    parser = etree.HTMLParser(encoding=encoding, remove_comments=True,
                              remove_pis=True, no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except (etree.LxmlError, ValueError) as e:
        raise ValueError(f"could not parse HTML: {e}") from e
    if root is None:
        raise ValueError("could not parse HTML: no document element")
    return root


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _events(el, in_heading=False):
    """Document-order walk: ('start', el), ('text', text, in_heading),
    ('end', el)."""
    yield ("start", el)
    in_heading = in_heading or el.tag in _HEADINGS
    if el.text and isinstance(el.tag, str):
        yield ("text", el.text, in_heading)
    for child in el:
        if isinstance(child.tag, str):
            yield from _events(child, in_heading)
        if child.tail:
            yield ("text", child.tail, in_heading)
    yield ("end", el)


class _Document:
    def __init__(self, root, path: Path):
        self.root = root
        self.path = path
        self.base = path.parent
        body = root.find("body")
        self.body = body if body is not None else root
        self.anchors: dict[str, object] = {}
        self.toc: list[TocEntry] = []
        for n, el in enumerate(self.body.iter(*_HEADINGS), 1):
            title = _text(el)
            if not title:
                continue
            target = el.get("id") or ""
            if not target or target in self.anchors:
                target = f"heading-{n}"
            self.anchors[target] = el
            self.toc.append(TocEntry(title, int(el.tag[1]), target))
        self._images: dict[str, bytes] = {}

    def _title(self) -> str:
        for el in self.root.xpath("//head/title"):
            if _text(el):
                return _text(el)
        return self.toc[0].title if self.toc else self.path.stem

    def _meta(self, name: str) -> str | None:
        for el in self.root.xpath("//head/meta[@name]"):
            if el.get("name", "").strip().lower() == name:
                value = " ".join((el.get("content") or "").split())
                if value:
                    return value
        return None

    def _language(self) -> str | None:
        lang = (self.root.get("lang")
                or self.root.get("{http://www.w3.org/XML/1998/namespace}lang")
                or "").strip()
        return lang or None

    def _has_text_between(self, parent: TocEntry, child: TocEntry) -> bool:
        p, c = self.anchors[parent.target], self.anchors[child.target]
        events = list(_events(self.body))
        end = next(i for i, e in enumerate(events)
                   if e[0] == "start" and e[1] is c)
        kind = "start" if p in c.iterancestors() else "end"
        start = next(i for i, e in enumerate(events)
                     if e[0] == kind and e[1] is p)
        return any(e[0] == "text" and not e[2] and e[1].strip()
                   for e in events[start:end])

    def _plan(self):
        levels = Counter(e.level for e in self.toc)
        if levels and max(levels.values()) >= _MIN_CHAPTERS:
            return plan_chapters(self.toc, [],
                                 has_text_between=self._has_text_between)
        first = self.toc[0].title if self.toc else None
        return plan_chapters([], [self.path.name],
                             first_heading=lambda _item: first)

    def _slices(self, starts: list[TocEntry]) -> list[str]:
        """The body HTML cut before each chapter's heading; the content before
        the first cut goes to the first chapter.

        Text in script, style or CDATA is serialized raw, so the comment
        marker carries a random token the file cannot know: no text in the
        file can pose as a marker."""
        token = secrets.token_hex(16)
        cut = re.compile(rf"<!--awiki-cut-{token}:(\d+)-->")
        for index, entry in enumerate(starts):
            el = self.anchors.get(entry.target)
            if index and el is not None:
                el.addprevious(etree.Comment(f"awiki-cut-{token}:{index}"))
        html = (self.body.text or "") + "".join(
            etree.tostring(ch, method="html", encoding="unicode")
            for ch in self.body)
        parts = cut.split(html)
        slices = [""] * len(starts)
        slices[0] = parts[0]
        for i in range(1, len(parts), 2):
            slices[int(parts[i])] += parts[i + 1]
        return slices

    def _resolve_image(self, src: str) -> str | None:
        src = src.split("#", 1)[0].split("?", 1)[0].strip()
        if not src or _SCHEME.match(src) or src.startswith(("/", "\\")):
            return None
        rel = posixpath.normpath(unquote(src).replace("\\", "/"))
        if rel == ".." or rel.startswith(("../", "/")):
            return None
        if posixpath.splitext(rel)[1].lower() not in _IMAGE_EXTS:
            return None
        if rel not in self._images:
            try:
                real = (self.base / rel).resolve(strict=True)
                if (not real.is_file()
                        or not real.is_relative_to(self.base.resolve())):
                    return None
                self._images[rel] = real.read_bytes()
            except OSError:
                return None
        return rel

    def convert(self) -> ConvertedDocument:
        title = self._title()
        plan = self._plan()
        slices = self._slices([p.start for p in plan])
        assets: dict[str, bytes] = {}
        chapters = []
        for planned, html in zip(plan, slices):
            md, images = html_to_markdown(html, self._resolve_image)
            for image in images:
                assets[image] = self._images[image]
            chapters.append(Chapter(planned.ordinal, planned.title, md, images))
        return ConvertedDocument(
            title=title, chapters=chapters, author=self._meta("author"),
            language=self._language(), assets=assets)
