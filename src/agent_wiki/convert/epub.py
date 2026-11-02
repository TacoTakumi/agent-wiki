"""EPUB to ``ConvertedDocument``.

The archive's central directory is checked against the size caps before
EbookLib reads any member. EbookLib then reads the package (metadata,
manifest, spine) and the table of contents: the EPUB3 navigation document
when there is one, else the NCX. EbookLib parses those with entity
resolution off; the content documents are parsed here the same way, so an
external entity never reads a local file and an entity bomb never expands.

The splitter plans the chapters from the TOC and the spine. Each content
document is cut at the anchors of the chapter starts that point into it, the
content before the first start goes to chapter 1, and every slice is rendered
through the shared markdown renderer. Image ``src`` values become archive
member paths (``OEBPS/images/a.jpg``); the bytes of each referenced image and
of the declared cover go into ``assets``.
"""
from __future__ import annotations

import posixpath
import re
import warnings
import zipfile
from pathlib import Path
from urllib.parse import unquote

from ebooklib import ITEM_COVER
from ebooklib.epub import EpubException, EpubHtml, EpubNav, EpubNcx, EpubReader
from lxml import etree

from agent_wiki.convert.markdown import html_to_markdown
from agent_wiki.convert.model import Chapter, ConvertedDocument, TocEntry
from agent_wiki.convert.split import plan_chapters

MAX_MEMBERS = 2000
MAX_UNCOMPRESSED = 256 * 1024 * 1024

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_CUT = re.compile(r"<!--awiki-cut:(\d+)-->")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def convert_epub(path: Path) -> ConvertedDocument:
    """Convert the EPUB at ``path``.

    Raises ValueError with a one-line reason when the file is not a zip, is
    over a cap, or is not a readable EPUB package.
    """
    path = Path(path)
    _check_archive(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader = EpubReader(str(path), {"ignore_ncx": True})
            book = reader.load()
    except (EpubException, KeyError, IndexError, AttributeError,
            etree.LxmlError) as e:
        raise ValueError(f"could not read EPUB: {e}") from e
    return _Book(book, reader.opf_dir, path).convert()


def _check_archive(path: Path) -> None:
    """Refuse a non-zip or an archive over a cap, reading only the central
    directory."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError(f"not a valid EPUB (zip) file: {e}") from e
    if len(infos) > MAX_MEMBERS:
        raise ValueError(f"EPUB has {len(infos)} members, over the "
                         f"{MAX_MEMBERS} member limit")
    total = sum(i.file_size for i in infos)
    if total > MAX_UNCOMPRESSED:
        raise ValueError(
            f"EPUB declares {total // (1024 * 1024)} MB uncompressed, over the "
            f"{MAX_UNCOMPRESSED // (1024 * 1024)} MB limit")


def _join(base: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(base, href))


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _parse(content: bytes):
    """The <body> of a content document, namespaces stripped, entities left
    unresolved."""
    parser = etree.XMLParser(recover=True, resolve_entities=False,
                             no_network=True)
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError:
        root = None
    if root is None:
        return etree.Element("body")
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    etree.cleanup_namespaces(root)
    body = root.find(".//body")
    return body if body is not None else root


def _anchor(body, fragment: str):
    if not fragment:
        return None
    found = body.xpath(".//*[@id=$f or @name=$f]", f=fragment)
    return found[0] if found else None


def _events(el, in_heading=False):
    """Document-order walk: ('start', el), ('text', text, in_heading)."""
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


class _Book:
    def __init__(self, book, opf_dir: str, path: Path):
        self.book = book
        self.path = path
        self.items = {_join(opf_dir, i.file_name): i for i in book.get_items()}
        self.spine = []
        for idref, _linear in book.spine:
            item = book.get_item_with_id(idref)
            if isinstance(item, EpubHtml):
                self.spine.append(_join(opf_dir, item.file_name))
        self.bodies = {p: _parse(self.items[p].content) for p in self.spine}
        navs = [i for i in book.get_items() if isinstance(i, EpubNav)]
        ncx = next((i for i in book.get_items() if isinstance(i, EpubNcx)), None)
        # Nav hrefs come back joined to the nav's folder, relative to the OPF;
        # NCX hrefs are as written, relative to the NCX file.
        base = opf_dir
        if not navs and ncx is not None:
            base = _join(opf_dir, posixpath.dirname(ncx.file_name))
        self.toc = list(self._flatten(book.toc, 1, base))

    def _flatten(self, nodes, level: int, base: str):
        for node in nodes:
            if isinstance(node, tuple):
                section, children = node
            else:
                section, children = node, []
            kids = list(self._flatten(children, level + 1, base))
            href = section.href or ""
            if href:
                file, _, frag = href.partition("#")
                target = _join(base, unquote(file)) + (f"#{frag}" if frag else "")
            else:
                target = kids[0].target if kids else ""
            yield TocEntry(" ".join((section.title or "").split()), level, target)
            yield from kids

    def _locate(self, entry: TocEntry):
        file, _, frag = entry.target.partition("#")
        body = self.bodies.get(file)
        return file, body, (_anchor(body, frag) if body is not None else None)

    def _first_heading(self, item: str) -> str | None:
        body = self.bodies.get(item)
        if body is None:
            return None
        for el in body.iter(*_HEADINGS):
            if _text(el):
                return _text(el)
        return None

    def _has_text_between(self, parent: TocEntry, child: TocEntry) -> bool:
        pfile, body, p = self._locate(parent)
        cfile, _, c = self._locate(child)
        if pfile != cfile or body is None or c is None:
            return True
        events = list(_events(body))
        end = next(i for i, e in enumerate(events) if e[0] == "start" and e[1] is c)
        start = 0
        if p is not None:
            kind = "start" if p in c.iterancestors() else "end"
            start = next(i for i, e in enumerate(events)
                         if e[0] == kind and e[1] is p)
        return any(e[0] == "text" and not e[2] and e[1].strip()
                   for e in events[start:end])

    def _slices(self, n_chapters: int, starts: list[TocEntry]):
        """(spine item, html) slices per chapter index."""
        for index, entry in enumerate(starts):
            file, body, el = self._locate(entry)
            if body is None:
                continue
            mark = etree.Comment(f"awiki-cut:{index}")
            if el is None or el is body:
                mark.tail, body.text = body.text, None
                body.insert(0, mark)
            else:
                el.addprevious(mark)
        slices: list[list[tuple[str, str]]] = [[] for _ in range(n_chapters)]
        current = 0
        for item in self.spine:
            body = self.bodies[item]
            html = (body.text or "") + "".join(
                etree.tostring(ch, method="html", encoding="unicode")
                for ch in body)
            parts = _CUT.split(html)
            slices[current].append((item, parts[0]))
            for i in range(1, len(parts), 2):
                current = int(parts[i])
                slices[current].append((item, parts[i + 1]))
        return slices

    def _resolver(self, item: str):
        def resolve(src: str) -> str | None:
            src = src.split("#", 1)[0].split("?", 1)[0]
            if not src or _SCHEME.match(src) or src.startswith("/"):
                return None
            target = _join(posixpath.dirname(item), unquote(src))
            return target if target in self.items else None
        return resolve

    def _meta(self, name: str) -> str | None:
        for value, _attrs in self.book.get_metadata("DC", name):
            if value and value.strip():
                return " ".join(value.split())
        return None

    def _cover(self) -> str | None:
        item = next(iter(self.book.get_items_of_type(ITEM_COVER)), None)
        if item is None:
            for _value, attrs in self.book.get_metadata("OPF", "meta"):
                if attrs.get("name") == "cover":
                    item = self.book.get_item_with_id(attrs.get("content"))
                    break
        if item is None or not (item.media_type or "").startswith("image/"):
            return None
        return next(p for p, i in self.items.items() if i is item)

    def convert(self) -> ConvertedDocument:
        title = self._meta("title") or next(
            (h for h in map(self._first_heading, self.spine) if h),
            self.path.stem)
        plan = plan_chapters(self.toc, self.spine,
                             first_heading=self._first_heading,
                             has_text_between=self._has_text_between)
        slices = self._slices(len(plan), [p.start for p in plan])
        assets: dict[str, bytes] = {}
        chapters = []
        for planned, parts in zip(plan, slices):
            blocks, images = [], []
            for item, html in parts:
                md, found = html_to_markdown(html, self._resolver(item))
                if md:
                    blocks.append(md)
                images += [i for i in found if i not in images]
            for image in images:
                assets[image] = self.items[image].content
            chapters.append(Chapter(planned.ordinal, planned.title,
                                    "\n\n".join(blocks), images))
        cover = self._cover()
        if cover:
            assets[cover] = self.items[cover].content
        return ConvertedDocument(
            title=title, chapters=chapters, author=self._meta("creator"),
            language=self._meta("language"), publisher=self._meta("publisher"),
            cover=cover, assets=assets)
