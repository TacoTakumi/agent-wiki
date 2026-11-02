"""HTML converter: one local HTML file to a ``ConvertedDocument``.

Every HTML file is built in-test by ``convert_fixtures.build_html`` or written
inline.
"""
from pathlib import Path

import pytest

from convert_fixtures import build_html, image_bytes

import agent_wiki.convert.html as html_mod
from agent_wiki.convert.html import convert_html


# ---------------------------------------------------------------- splitting

def test_nav_block_and_four_h2_sections_give_four_chapters(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html"))
    assert [c.ordinal for c in doc.chapters] == [1, 2, 3, 4]
    assert [c.title for c in doc.chapters] == [
        "First Section", "Second Section", "Third Section", "Fourth Section"]
    for n, chapter in enumerate(doc.chapters, 1):
        assert f"SECTION{n}" in chapter.markdown
        others = {f"SECTION{m}" for m in range(1, 5)} - {f"SECTION{n}"}
        assert not any(o in chapter.markdown for o in others)


def test_whole_file_is_kept_nav_and_intro_in_chapter_one_footer_in_last(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html"))
    first, last = doc.chapters[0].markdown, doc.chapters[-1].markdown
    assert "NAVTEXT" in first and "INTRO" in first
    assert "# Sample Document" in first
    assert first.index("NAVTEXT") < first.index("SECTION1")
    assert "FOOTTEXT" in last
    assert all("NAVTEXT" not in c.markdown for c in doc.chapters[1:])


def test_chapter_markdown_has_atx_headings_and_no_tags(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html", image=None))
    assert doc.chapters[1].markdown.startswith("## Second Section")
    for chapter in doc.chapters:
        assert "<" not in chapter.markdown


def test_head_content_is_not_rendered(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text(
        "<html><head><title>Head Title</title><style>.x{color:red}</style>"
        "</head><body><h1>Only</h1><p>Body text.</p></body></html>",
        encoding="utf-8")
    md = convert_html(path).chapters[0].markdown
    assert "color:red" not in md
    assert "Head Title" not in md
    assert "Body text." in md


def test_two_heading_file_is_one_chapter(tmp_path):
    sections = [(2, "Alpha", "Alpha text. AAA"), (2, "Beta", "Beta text. BBB")]
    doc = convert_html(build_html(tmp_path / "two.html", sections=sections))
    assert len(doc.chapters) == 1
    chapter = doc.chapters[0]
    assert chapter.ordinal == 1
    assert chapter.title == "Alpha"
    for marker in ("NAVTEXT", "AAA", "BBB", "FOOTTEXT"):
        assert marker in chapter.markdown


def test_fewer_than_three_headings_at_every_level_is_one_chapter(tmp_path):
    sections = [(1, "Top", "t"), (2, "A", "a"), (2, "B", "b"),
                (3, "A1", "x"), (3, "B1", "y")]
    doc = convert_html(build_html(tmp_path / "few.html", sections=sections))
    assert len(doc.chapters) == 1
    assert doc.chapters[0].title == "Top"


def test_no_headings_is_one_chapter_titled_part_01(tmp_path):
    path = tmp_path / "plain.html"
    path.write_text("<html><body><p>Just text. PLAIN</p></body></html>",
                    encoding="utf-8")
    doc = convert_html(path)
    assert len(doc.chapters) == 1
    assert doc.chapters[0].title == "Part 01"
    assert "PLAIN" in doc.chapters[0].markdown


def test_deeper_headings_merge_into_their_chapter(tmp_path):
    sections = [(2, "One", "ONE"), (3, "One A", "ONEA"), (2, "Two", "TWO"),
                (2, "Three", "THREE"), (3, "Three A", "THREEA"),
                (3, "Three B", "THREEB")]
    doc = convert_html(build_html(tmp_path / "d.html", sections=sections,
                                  nav=False, image=None))
    assert [c.title for c in doc.chapters] == ["One", "Two", "Three"]
    assert "ONEA" in doc.chapters[0].markdown
    assert "### One A" in doc.chapters[0].markdown
    assert "THREEA" in doc.chapters[2].markdown
    assert "THREEB" in doc.chapters[2].markdown


def test_lone_child_heading_with_no_text_between_joins_titles(tmp_path):
    path = tmp_path / "join.html"
    path.write_text(
        "<html><body>"
        "<h2>CHAPTER: 1</h2><h3>Early Life</h3><p>A</p>"
        "<h2>CHAPTER: 2</h2><p>text between</p><h3>Later</h3><p>B</p>"
        "<h2>CHAPTER: 3</h2><p>C</p>"
        "</body></html>", encoding="utf-8")
    titles = [c.title for c in convert_html(path).chapters]
    assert titles == ["Chapter 1: Early Life", "CHAPTER: 2", "CHAPTER: 3"]


def test_headings_nested_in_sections_still_split(tmp_path):
    body = "".join(
        f"<section><h2>Part {n}</h2><p>Text NESTED{n}</p></section>"
        for n in range(1, 4))
    path = tmp_path / "nested.html"
    path.write_text(f"<html><body><header>HEADERTEXT</header>{body}"
                    "</body></html>", encoding="utf-8")
    doc = convert_html(path)
    assert len(doc.chapters) == 3
    assert "HEADERTEXT" in doc.chapters[0].markdown
    for n, chapter in enumerate(doc.chapters, 1):
        assert f"NESTED{n}" in chapter.markdown


def test_duplicate_heading_ids_and_missing_ids_still_split(tmp_path):
    path = tmp_path / "ids.html"
    path.write_text(
        '<html><body><h2 id="x">A</h2><p>AAA</p><h2 id="x">B</h2><p>BBB</p>'
        "<h2>C</h2><p>CCC</p><h2></h2><p>EMPTY</p></body></html>",
        encoding="utf-8")
    doc = convert_html(path)
    assert [c.title for c in doc.chapters] == ["A", "B", "C"]
    assert "BBB" in doc.chapters[1].markdown
    assert "EMPTY" in doc.chapters[2].markdown


# ---------------------------------------------------------------- title

def test_title_comes_from_the_title_element(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html",
                                  title="  The   Real Title "))
    assert doc.title == "The Real Title"


def test_title_falls_back_to_the_first_heading(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html", title=None))
    assert doc.title == "Sample Document"


def test_blank_title_element_falls_back_to_the_first_heading(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html", title="  "))
    assert doc.title == "Sample Document"


def test_title_falls_back_to_the_file_stem(tmp_path):
    path = tmp_path / "my-notes.html"
    path.write_text("<html><body><p>No headings.</p></body></html>",
                    encoding="utf-8")
    assert convert_html(path).title == "my-notes"


def test_metadata_from_lang_and_author_meta(tmp_path):
    path = tmp_path / "m.htm"
    path.write_text(
        '<html lang="fr"><head><meta name="author" content="Ann Writer">'
        "</head><body><h1>Titre</h1><p>Texte.</p></body></html>",
        encoding="utf-8")
    doc = convert_html(path)
    assert doc.language == "fr"
    assert doc.author == "Ann Writer"
    assert doc.publisher is None
    assert doc.cover is None


def test_metadata_absent_is_none(tmp_path):
    doc = convert_html(build_html(tmp_path / "doc.html"))
    assert doc.author is None and doc.language is None


# ---------------------------------------------------------------- encoding

def test_utf8_without_charset_meta_is_read_as_utf8(tmp_path):
    path = tmp_path / "u.html"
    path.write_bytes("<html><body><h1>Café</h1><p>naïve</p></body></html>"
                     .encode("utf-8"))
    doc = convert_html(path)
    assert doc.title == "Café"
    assert "naïve" in doc.chapters[0].markdown


def test_declared_legacy_charset_is_honoured(tmp_path):
    path = tmp_path / "l.html"
    path.write_bytes(
        '<html><head><meta charset="iso-8859-1"></head><body><h1>Caf\xe9</h1>'
        "</body></html>".encode("latin-1"))
    assert convert_html(path).title == "Café"


def test_xml_declaration_is_accepted(tmp_path):
    path = tmp_path / "x.html"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>X Doc</title>'
        "</head><body><h1>Head</h1><p>XBODY</p></body></html>",
        encoding="utf-8")
    doc = convert_html(path)
    assert doc.title == "X Doc"
    assert "XBODY" in doc.chapters[0].markdown


# ---------------------------------------------------------------- images

def test_local_image_is_resolved_relative_to_the_file_and_collected(tmp_path):
    path = build_html(tmp_path / "doc.html")
    doc = convert_html(path)
    first = doc.chapters[0]
    assert first.images == ["images/pic.png"]
    assert "![picture](images/pic.png)" in first.markdown
    assert doc.assets == {"images/pic.png":
                          (tmp_path / "images" / "pic.png").read_bytes()}
    assert all(c.images == [] for c in doc.chapters[1:])


def test_remote_missing_escaping_and_non_image_sources_are_kept_as_written(
        tmp_path):
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "outside.png").write_bytes(image_bytes("png"))
    sub = tmp_path / "site"
    sub.mkdir()
    (sub / "notes.txt").write_text("not an image", encoding="utf-8")
    path = sub / "doc.html"
    path.write_text(
        "<html><body><h1>T</h1>"
        '<p><img src="https://example.com/a.png" alt="web"></p>'
        '<p><img src="missing.png" alt="gone"></p>'
        '<p><img src="../outside.png" alt="up"></p>'
        '<p><img src="/etc/hostname" alt="abs"></p>'
        '<p><img src="notes.txt" alt="txt"></p>'
        '<p><img src="data:image/png;base64,AAAA" alt="data"></p>'
        "</body></html>", encoding="utf-8")
    doc = convert_html(path)
    md = doc.chapters[0].markdown
    assert "![web](https://example.com/a.png)" in md
    assert "![gone](missing.png)" in md
    assert "![up](../outside.png)" in md
    assert "![abs](/etc/hostname)" in md
    assert "![txt](notes.txt)" in md
    assert doc.chapters[0].images == []
    assert doc.assets == {}


def test_image_src_with_dot_segments_and_escapes_is_normalised(tmp_path):
    img = tmp_path / "img dir" / "a b.png"
    img.parent.mkdir()
    img.write_bytes(image_bytes("png", 90))
    path = tmp_path / "doc.html"
    path.write_text(
        '<html><body><h1>T</h1><p><img src="./x/../img%20dir/a%20b.png?v=2"'
        ' alt="pic"></p></body></html>', encoding="utf-8")
    doc = convert_html(path)
    assert doc.chapters[0].images == ["img dir/a b.png"]
    assert doc.assets["img dir/a b.png"] == img.read_bytes()


def test_symlinked_image_pointing_outside_is_not_read(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(image_bytes("png"))
    site = tmp_path / "site"
    site.mkdir()
    link = site / "link.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not available")
    path = site / "doc.html"
    path.write_text('<html><body><h1>T</h1><p><img src="link.png" alt="l">'
                    "</p></body></html>", encoding="utf-8")
    doc = convert_html(path)
    assert doc.assets == {}
    assert doc.chapters[0].images == []


# ---------------------------------------------------------------- errors

def test_missing_file_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="could not read HTML"):
        convert_html(tmp_path / "nope.html")


def test_empty_file_raises_value_error(tmp_path):
    path = tmp_path / "empty.html"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="HTML"):
        convert_html(path)


def test_binary_file_raises_value_error(tmp_path):
    path = tmp_path / "x.html"
    path.write_bytes(b"%PDF-1.7\n\x00\x01\x02binary")
    with pytest.raises(ValueError, match="not an HTML file"):
        convert_html(path)


# ---------------------------------------------------------------- structure

def test_module_does_not_use_the_boilerplate_remover():
    source = Path(html_mod.__file__).read_text(encoding="utf-8")
    assert "trafil" not in source.lower()
