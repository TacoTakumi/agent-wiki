"""EPUB converter and the shared markdown renderer.

Every EPUB is built in-test by ``convert_fixtures.build_epub``; the crafted
zips for the size caps are built here.
"""
import struct
import time
import zipfile

import pytest

from convert_fixtures import (NavPoint, XhtmlFile, build_epub, chapter_body)

from agent_wiki.convert.epub import (MAX_MEMBERS, MAX_UNCOMPRESSED,
                                     convert_epub)
from agent_wiki.convert.markdown import html_to_markdown


def _member(path, name):
    with zipfile.ZipFile(path) as zf:
        return zf.read(name)


def _rewrite(path, changes):
    """Rewrite members of the zip at ``path``; ``changes`` maps a member name
    to a function from its text to the new text."""
    with zipfile.ZipFile(path) as zf:
        members = [(i, zf.read(i.filename)) for i in zf.infolist()]
    with zipfile.ZipFile(path, "w") as zf:
        for info, data in members:
            if info.filename in changes:
                data = changes[info.filename](data.decode()).encode()
            zf.writestr(info, data)


# ---------------------------------------------------------------- renderer

def test_renderer_writes_atx_headings_dash_bullets_emphasis_and_links():
    md, images = html_to_markdown(chapter_body(1))
    assert "## Chapter One" in md
    assert "### Section 1.1" in md
    assert "1. first" in md and "2. second" in md
    assert "- apple" in md and "- pear" in md
    assert "*emphasis*" in md and "**strength**" in md
    assert "[a link](https://example.com/1)" in md
    assert "<" not in md
    assert images == []


def test_renderer_maps_resolved_image_sources_and_lists_them_once():
    html = ('<p><img src="a.jpg" alt="one"/></p><p><img src="a.jpg" alt="two"/>'
            '</p><p><img src="http://x/y.png" alt="web"/></p>')
    md, images = html_to_markdown(
        html, resolve_image=lambda src: None if ":" in src else f"OEBPS/{src}")
    assert "![one](OEBPS/a.jpg)" in md and "![two](OEBPS/a.jpg)" in md
    assert "![web](http://x/y.png)" in md
    assert images == ["OEBPS/a.jpg"]


# ---------------------------------------------------------------- reading

def test_default_epub_gives_three_chapters_and_metadata(tmp_path):
    doc = convert_epub(build_epub(tmp_path / "book.epub"))
    assert doc.title == "A Book"
    assert doc.metadata == {"author": "Jane Author", "language": "en",
                            "publisher": "Fixture Press"}
    assert [(c.ordinal, c.title) for c in doc.chapters] == [
        (1, "Chapter One"), (2, "Chapter Two"), (3, "Chapter Three")]
    for n, ch in enumerate(doc.chapters, 1):
        assert f"BODY{n}" in ch.markdown
        assert f"## {ch.title}" in ch.markdown
        assert "- apple" in ch.markdown and "1. first" in ch.markdown
        assert "<" not in ch.markdown
    assert "BODY2" not in doc.chapters[0].markdown


def test_title_falls_back_to_first_heading(tmp_path):
    doc = convert_epub(build_epub(tmp_path / "book.epub", title=None))
    assert doc.title == "Chapter One"


def _toc(prefix):
    return [NavPoint(f"{prefix} {n}", f"text/ch{n:02d}.xhtml#c{n}")
            for n in (1, 2, 3)]


def test_nav_wins_over_ncx(tmp_path):
    path = build_epub(tmp_path / "book.epub", n_chapters=3,
                      toc=_toc("Ncx"), nav_toc=_toc("Nav"))
    doc = convert_epub(path)
    assert [c.title for c in doc.chapters] == ["Nav 1", "Nav 2", "Nav 3"]


def test_ncx_alone_gives_the_toc(tmp_path):
    path = build_epub(tmp_path / "book.epub", toc=_toc("Ncx"), nav=False)
    doc = convert_epub(path)
    assert [c.title for c in doc.chapters] == ["Ncx 1", "Ncx 2", "Ncx 3"]
    assert "BODY2" in doc.chapters[1].markdown


def test_two_anchors_in_one_file_slice_at_each_anchor(tmp_path):
    files = [
        XhtmlFile("text/ab.xhtml",
                  '<p>PRE text</p>\n<h2 id="a">Part A</h2>\n<p>TEXTA here</p>\n'
                  '<div class="wrap"><h2 id="b">Part B</h2>\n'
                  "<p>TEXTB here</p></div>\n"),
        XhtmlFile("text/c.xhtml", '<h2 id="c">Part C</h2><p>TEXTC here</p>'),
    ]
    toc = [NavPoint("Part A", "text/ab.xhtml#a"),
           NavPoint("Part B", "text/ab.xhtml#b"),
           NavPoint("Part C", "text/c.xhtml#c")]
    doc = convert_epub(build_epub(tmp_path / "b.epub", files=files, toc=toc))
    a, b, c = (ch.markdown for ch in doc.chapters)
    assert "PRE text" in a and "TEXTA" in a
    assert "TEXTB" not in a and "TEXTC" not in a
    assert "TEXTB" in b and "## Part B" in b
    assert "TEXTA" not in b and "PRE" not in b and "TEXTC" not in b
    assert "TEXTC" in c and "TEXTB" not in c


def test_lone_child_title_joins_when_no_text_between(tmp_path):
    files, toc = [], []
    for n in (1, 2, 3):
        gap = "<p>An intro paragraph.</p>" if n == 3 else ""
        files.append(XhtmlFile(
            f"text/ch{n}.xhtml",
            f'<h2 id="c{n}">CHAPTER: {n}</h2>{gap}'
            f'<h3 id="t{n}">Title {n}</h3><p>BODY{n}</p>'))
        toc.append(NavPoint(f"CHAPTER: {n}", f"text/ch{n}.xhtml#c{n}",
                            [NavPoint(f"Title {n}", f"text/ch{n}.xhtml#t{n}")]))
    doc = convert_epub(build_epub(tmp_path / "b.epub", files=files, toc=toc))
    assert [c.title for c in doc.chapters] == [
        "Chapter 1: Title 1", "Chapter 2: Title 2", "CHAPTER: 3"]


def test_no_toc_falls_back_to_spine_with_first_headings(tmp_path):
    files = [XhtmlFile(f"text/p{n}.xhtml", f"<h1>Heading {n}</h1><p>P{n}</p>")
             for n in (1, 2)]
    doc = convert_epub(build_epub(tmp_path / "b.epub", files=files, toc=[]))
    assert [c.title for c in doc.chapters] == ["Heading 1", "Heading 2"]
    assert "P2" in doc.chapters[1].markdown


# ---------------------------------------------------------------- images

def test_referenced_images_are_collected_and_unreferenced_are_not(tmp_path):
    path = build_epub(tmp_path / "book.epub", cover=None)
    doc = convert_epub(path)
    first = doc.chapters[0]
    assert first.images == ["OEBPS/images/a.jpg"]
    assert "![figure 1](OEBPS/images/a.jpg)" in first.markdown
    assert doc.assets == {
        "OEBPS/images/a.jpg": _member(path, "OEBPS/images/a.jpg")}
    assert all("b.png" not in img for ch in doc.chapters for img in ch.images)


@pytest.mark.parametrize("style", ["epub2", "epub3"])
def test_declared_cover_is_found(tmp_path, style):
    path = build_epub(tmp_path / "book.epub", cover=style)
    doc = convert_epub(path)
    assert doc.cover == "OEBPS/images/cover.jpg"
    assert doc.assets[doc.cover] == _member(path, "OEBPS/images/cover.jpg")


def test_no_cover_declared_gives_none(tmp_path):
    assert convert_epub(build_epub(tmp_path / "b.epub", cover=None)).cover is None


# ---------------------------------------------------------------- safety

@pytest.fixture
def forbid_member_reads(monkeypatch):
    """Call after building the zip: any later member read fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("a zip member was read")
    return lambda: monkeypatch.setattr(zipfile.ZipFile, "open", refuse)


def test_more_than_the_member_limit_is_refused_before_reading(
        tmp_path, forbid_member_reads):
    path = tmp_path / "many.epub"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(MAX_MEMBERS + 1):
            zf.writestr(f"m{i}.txt", b"")
    forbid_member_reads()
    with pytest.raises(ValueError, match=str(MAX_MEMBERS)):
        convert_epub(path)


def test_declared_size_over_the_limit_is_refused_before_reading(
        tmp_path, forbid_member_reads):
    path = tmp_path / "big.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("big.bin", b"x", compress_type=zipfile.ZIP_STORED)
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    # Uncompressed size sits 24 bytes into the central directory header.
    data[central + 24:central + 28] = struct.pack("<I", 300 * 1024 * 1024)
    path.write_bytes(bytes(data))
    assert MAX_UNCOMPRESSED == 256 * 1024 * 1024
    forbid_member_reads()
    with pytest.raises(ValueError, match="256 MB"):
        convert_epub(path)


def test_external_and_bomb_entities_are_not_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRETHOST")
    ext = f'<!ENTITY xxe SYSTEM "{secret.as_uri()}">'
    lols = '<!ENTITY lol0 "lol">' + "".join(
        f'<!ENTITY lol{i} "{f"&lol{i - 1};" * 10}">' for i in range(1, 10))
    path = build_epub(tmp_path / "book.epub")
    _rewrite(path, {
        "OEBPS/content.opf": lambda t: t.replace(
            "?>\n", f"?>\n<!DOCTYPE package [{ext}]>\n", 1).replace(
            "<dc:title>A Book</dc:title>", "<dc:title>&xxe;</dc:title>"),
        "OEBPS/text/ch01.xhtml": lambda t: t.replace(
            "<!DOCTYPE html>", f"<!DOCTYPE html [{ext}]>").replace(
            "</body>", "<p>&xxe;</p></body>"),
        "OEBPS/text/ch02.xhtml": lambda t: t.replace(
            "<!DOCTYPE html>", f"<!DOCTYPE html [{lols}]>").replace(
            "</body>", "<p>&lol9;</p></body>"),
    })
    start = time.monotonic()
    doc = convert_epub(path)
    assert time.monotonic() - start < 5
    assert "SECRETHOST" not in doc.title
    assert all("SECRETHOST" not in c.markdown for c in doc.chapters)
    assert all("lollol" not in c.markdown for c in doc.chapters)


def test_a_file_that_is_not_a_zip_raises_the_conversion_error(tmp_path):
    path = tmp_path / "x.epub"
    path.write_text("just some text, not a zip\n")
    with pytest.raises(ValueError, match="not a valid EPUB"):
        convert_epub(path)
