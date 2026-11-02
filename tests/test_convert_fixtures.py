"""Smoke tests for the conversion contract and the in-test fixture builders.

The converters, the splitter and the vault writer are built against these
dataclasses and fixtures, so this checks that the package imports, the
dataclasses hold what the writer needs, and each fixture opens in the library
the matching converter reads it with.
"""
import warnings
import zipfile
from html.parser import HTMLParser

import pymupdf
from ebooklib import ITEM_COVER, ITEM_DOCUMENT, ITEM_IMAGE, epub

from convert_fixtures import (NavPoint, XhtmlFile, build_epub, build_html,
                              build_pdf, chapter_body)

from agent_wiki.convert import Chapter, ConvertedDocument, TocEntry

DC = "http://purl.org/dc/elements/1.1/"
OPF = "http://www.idpf.org/2007/opf"


def _read_epub(path):
    # EbookLib warns about future option defaults; that is noise here.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return epub.read_epub(str(path))


def _dc(book, name):
    values = book.get_metadata("DC", name)
    return values[0][0] if values else None


# ---------------------------------------------------------------- contract

def test_contract_dataclasses_hold_document_shape():
    toc = [TocEntry("Chapter One", 1, target="OEBPS/text/ch01.xhtml#c1"),
           TocEntry("Chapter Two", 1, page=3)]
    assert toc[0].page is None and toc[1].target == ""
    ch = Chapter(1, "Chapter One", "## Chapter One\n\ntext\n",
                 images=["OEBPS/images/a.jpg"])
    doc = ConvertedDocument(
        title="A Book", chapters=[ch, Chapter(2, "Chapter Two", "text")],
        author="Jane Author", language="en", publisher="Fixture Press",
        cover="OEBPS/images/cover.jpg",
        assets={"OEBPS/images/a.jpg": b"a", "OEBPS/images/cover.jpg": b"c"})
    assert [c.ordinal for c in doc.chapters] == [1, 2]
    assert doc.chapters[1].images == []
    assert doc.metadata == {"author": "Jane Author", "language": "en",
                            "publisher": "Fixture Press"}


def test_metadata_drops_fields_the_format_lacks():
    doc = ConvertedDocument(title="T", chapters=[], language="en")
    assert doc.metadata == {"language": "en"}
    assert doc.cover is None and doc.assets == {}


# ---------------------------------------------------------------- EPUB

def test_default_epub_opens_with_metadata_toc_cover_and_images(tmp_path):
    path = build_epub(tmp_path / "book.epub")
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist()[0] == "mimetype"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        ch1 = zf.read("OEBPS/text/ch01.xhtml").decode()
    assert 'id="c1"' in ch1 and '<img src="../images/a.jpg"' in ch1

    book = _read_epub(path)
    assert _dc(book, "title") == "A Book"
    assert _dc(book, "creator") == "Jane Author"
    assert _dc(book, "language") == "en"
    assert _dc(book, "publisher") == "Fixture Press"
    assert [(t.title, t.href) for t in book.toc] == [
        ("Chapter One", "text/ch01.xhtml#c1"),
        ("Chapter Two", "text/ch02.xhtml#c2"),
        ("Chapter Three", "text/ch03.xhtml#c3")]
    assert len(book.spine) == 3
    meta = book.metadata[OPF]["meta"]
    assert (None, {"name": "cover", "content": "cover-img"}) in meta
    images = {i.file_name for i in book.get_items_of_type(ITEM_IMAGE)}
    assert images == {"images/a.jpg", "images/b.png", "images/cover.jpg"}


def test_epub_variants_nav_ncx_cover_and_chapter_count(tmp_path):
    ncx_only = _read_epub(build_epub(tmp_path / "a.epub", nav=False,
                                     n_chapters=5))
    assert len(ncx_only.toc) == 5
    docs = [i.file_name for i in ncx_only.get_items_of_type(ITEM_DOCUMENT)]
    assert "nav.xhtml" not in docs

    epub3 = _read_epub(build_epub(tmp_path / "b.epub", ncx=False,
                                  cover="epub3"))
    assert len(epub3.toc) == 3
    assert [i.file_name for i in epub3.get_items_of_type(ITEM_COVER)] == [
        "images/cover.jpg"]

    bare = _read_epub(build_epub(tmp_path / "c.epub", ncx=False, nav=False,
                                 cover=None))
    assert bare.toc == [] and len(bare.spine) == 3

    nav_wins = _read_epub(build_epub(
        tmp_path / "d.epub",
        nav_toc=[NavPoint("Nav Entry", "text/ch01.xhtml#c1")]))
    assert [t.title for t in nav_wins.toc] == ["Nav Entry"]


def test_epub_custom_files_share_a_spine_file_with_nested_toc(tmp_path):
    body = chapter_body(1, "Part A") + chapter_body(2, "Part B")
    files = [XhtmlFile("text/all.xhtml", body, "All")]
    toc = [NavPoint("Part A", "text/all.xhtml#c1",
                    [NavPoint("Section 1.1", "text/all.xhtml#c1s1")]),
           NavPoint("Part B", "text/all.xhtml#c2")]
    book = _read_epub(build_epub(tmp_path / "e.epub", files=files, toc=toc,
                                 nav=False, title=None, publisher=None))
    assert _dc(book, "title") is None and _dc(book, "publisher") is None
    part_a, part_b = book.toc
    section, children = part_a
    assert section.title == "Part A"
    assert [c.title for c in children] == ["Section 1.1"]
    assert part_b.href == "text/all.xhtml#c2"
    assert len(book.spine) == 1


# ---------------------------------------------------------------- HTML

class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.title, self._in_title = [], "", False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def test_html_fixture_parses_with_title_headings_and_image(tmp_path):
    path = build_html(tmp_path / "page.html")
    parser = _Collect()
    parser.feed(path.read_text(encoding="utf-8"))
    assert parser.title == "Sample Document"
    heads = [t for t, _ in parser.tags if t in ("h1", "h2")]
    assert heads == ["h1", "h2", "h2", "h2", "h2"]
    img = next(a for t, a in parser.tags if t == "img")
    assert (tmp_path / img["src"]).is_file()
    assert "NAVTEXT" in path.read_text(encoding="utf-8")


def test_html_fixture_options(tmp_path):
    path = build_html(tmp_path / "two.html", title=None, nav=False,
                      image=None, sections=[(2, "One", "a"), (2, "Two", "b")])
    parser = _Collect()
    parser.feed(path.read_text(encoding="utf-8"))
    assert parser.title == ""
    assert [t for t, _ in parser.tags if t.startswith("h")] == [
        "html", "head", "h2", "h2"]
    assert not any(t in ("img", "nav") for t, _ in parser.tags)


# ---------------------------------------------------------------- PDF

def test_pdf_fixture_has_outline_pages_and_title(tmp_path):
    path = build_pdf(tmp_path / "book.pdf")
    with pymupdf.open(path) as doc:
        assert doc.page_count == 6
        assert doc.get_toc() == [[1, "Chapter One", 1],
                                 [1, "Chapter Two", 3],
                                 [1, "Chapter Three", 5]]
        assert doc.metadata["title"] == "A PDF Book"
        assert "Chapter Two" in doc[2].get_text()
        assert "PAGE4" in doc[3].get_text()


def test_pdf_fixture_without_outline_or_title(tmp_path):
    path = build_pdf(tmp_path / "flat.pdf", outline=False, title="")
    with pymupdf.open(path) as doc:
        assert doc.get_toc() == []
        assert doc.metadata["title"] == ""
        assert "Chapter One" in doc[0].get_text()
