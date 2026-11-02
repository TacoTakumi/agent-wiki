"""Text that comes before the first child element of a body or a slice.

The EPUB and HTML converters serialize each body to HTML and hand that to
markdownify. Text before the first child element is not part of any child,
so it must be serialized as text too: escaped markup in it ('&lt;!--',
'&lt;b&gt;', '&amp;') is book text, not markup. If it were joined in raw, an
escaped '<!--' would open a real comment and swallow the rest of the file.

What markdownify writes for a literal '<' is its own behaviour for text in
an element; these tests pin that leading text converts exactly like the same
text wrapped in a <p>, and that nothing after it is lost.
"""
import dataclasses

import pytest
from convert_fixtures import NavPoint, XhtmlFile, build_epub, chapter_body, \
    default_epub_content

from agent_wiki.convert.epub import convert_epub
from agent_wiki.convert.html import convert_html

# Escaped markup as written in the source file, and what it reads as.
ESCAPED = {
    "comment": ("&lt;!-- x", "<!-- x"),
    "bold": ("&lt;b&gt;bold&lt;/b&gt;", "<b>bold</b>"),
    "amp": ("&amp;", "&"),
}


def _lead(kind):
    return f"Lead {ESCAPED[kind][0]} AFTERLEAD"


def _shape(doc):
    return [(c.ordinal, c.title, c.markdown) for c in doc.chapters]


# ---------------------------------------------------------------- HTML


def _html(tmp_path, name, lead, middle=""):
    path = tmp_path / name
    path.write_text(
        "<html><head><title>T</title></head><body>"
        f"{lead}\n<h1 id=\"a\">One</h1><p>BODY1</p>{middle}"
        "<h1 id=\"b\">Two</h1><p>BODY2</p>"
        "<h1 id=\"c\">Three</h1><p>BODY3</p></body></html>",
        encoding="utf-8")
    return convert_html(path)


def test_html_body_opening_with_escaped_comment_keeps_every_section(tmp_path):
    doc = _html(tmp_path, "doc.html", "Lead &lt;!-- oops; tail text kept")
    assert [c.title for c in doc.chapters] == ["One", "Two", "Three"]
    first = doc.chapters[0].markdown
    assert "Lead <!-- oops; tail text kept" in first
    assert "# One" in first and "BODY1" in first
    for n, chapter in enumerate(doc.chapters[1:], 2):
        assert f"BODY{n}" in chapter.markdown


@pytest.mark.parametrize("kind", sorted(ESCAPED))
def test_html_leading_body_text_converts_like_a_paragraph(tmp_path, kind):
    bare = _html(tmp_path, "bare.html", _lead(kind))
    wrapped = _html(tmp_path, "wrapped.html", f"<p>{_lead(kind)}</p>")
    assert _shape(bare) == _shape(wrapped)
    first = bare.chapters[0].markdown
    assert "AFTERLEAD" in first and "BODY1" in first
    assert f"Lead {ESCAPED[kind][1]} AFTERLEAD" in first
    assert [f"BODY{n}" in c.markdown for n, c in enumerate(bare.chapters, 1)] \
        == [True, True, True]


@pytest.mark.parametrize("kind", sorted(ESCAPED))
def test_html_text_before_first_element_of_later_slice(tmp_path, kind):
    # Text right after chapter two's heading, before its paragraph: the
    # first text of that slice that is not inside an element.
    def doc(name, text):
        path = tmp_path / name
        path.write_text(
            "<html><head><title>T</title></head><body>"
            "<h1 id=\"a\">One</h1><p>BODY1</p>"
            f"<div><h1 id=\"b\">Two</h1>{text}<p>BODY2</p></div>"
            "<h1 id=\"c\">Three</h1><p>BODY3</p></body></html>",
            encoding="utf-8")
        return convert_html(path)

    bare = doc("bare.html", _lead(kind))
    wrapped = doc("wrapped.html", f"<p>{_lead(kind)}</p>")
    assert _shape(bare) == _shape(wrapped)
    second = bare.chapters[1].markdown
    assert "AFTERLEAD" in second and "BODY2" in second
    assert "BODY3" in bare.chapters[2].markdown


# ---------------------------------------------------------------- EPUB


def _epub(tmp_path, name, files, toc):
    return convert_epub(build_epub(tmp_path / name, files=files, toc=toc,
                                   cover=None))


def _prefixed(index, prefix):
    """The default three-chapter content with ``prefix`` put before the
    heading of file ``index``."""
    files, toc = default_epub_content(image_chapters={})
    f = files[index]
    files[index] = dataclasses.replace(f, body=prefix + "\n" + f.body)
    return files, toc


@pytest.mark.parametrize("kind", sorted(ESCAPED))
def test_epub_leading_body_text_of_first_file(tmp_path, kind):
    bare = _epub(tmp_path, "bare.epub", *_prefixed(0, _lead(kind)))
    wrapped = _epub(tmp_path, "wrapped.epub",
                    *_prefixed(0, f"<p>{_lead(kind)}</p>"))
    assert _shape(bare) == _shape(wrapped)
    first = bare.chapters[0].markdown
    assert "AFTERLEAD" in first
    assert "BODY1" in first and "Section 1.1" in first
    assert "BODY2" in bare.chapters[1].markdown


@pytest.mark.parametrize("kind", sorted(ESCAPED))
def test_epub_leading_text_of_later_file_before_its_heading(tmp_path, kind):
    # The TOC points at chapter two's heading, so the text before it closes
    # chapter one.
    bare = _epub(tmp_path, "bare.epub", *_prefixed(1, _lead(kind)))
    wrapped = _epub(tmp_path, "wrapped.epub",
                    *_prefixed(1, f"<p>{_lead(kind)}</p>"))
    assert _shape(bare) == _shape(wrapped)
    assert bare.chapters[0].markdown.endswith("AFTERLEAD")
    assert "BODY2" in bare.chapters[1].markdown
    assert "Section 2.1" in bare.chapters[1].markdown


@pytest.mark.parametrize("kind", sorted(ESCAPED))
def test_epub_leading_text_of_file_continuing_a_chapter(tmp_path, kind):
    # Chapter two spans two files; the second opens with bare text.
    def files(lead):
        return [
            XhtmlFile("text/ch01.xhtml", chapter_body(1), "Chapter One"),
            XhtmlFile("text/ch02.xhtml", chapter_body(2), "Chapter Two"),
            XhtmlFile("text/ch02b.xhtml", f"{lead}\n<p>CONTINUED</p>\n",
                      "Chapter Two"),
            XhtmlFile("text/ch03.xhtml", chapter_body(3), "Chapter Three"),
        ]

    toc = [NavPoint("Chapter One", "text/ch01.xhtml#c1"),
           NavPoint("Chapter Two", "text/ch02.xhtml#c2"),
           NavPoint("Chapter Three", "text/ch03.xhtml#c3")]
    bare = _epub(tmp_path, "bare.epub", files(_lead(kind)), toc)
    wrapped = _epub(tmp_path, "wrapped.epub",
                    files(f"<p>{_lead(kind)}</p>"), toc)
    assert _shape(bare) == _shape(wrapped)
    second = bare.chapters[1].markdown
    assert "AFTERLEAD" in second and "CONTINUED" in second
    assert "BODY3" in bare.chapters[2].markdown


@pytest.mark.parametrize("kind", sorted(ESCAPED))
def test_epub_leading_text_of_file_that_starts_a_chapter(tmp_path, kind):
    # The TOC points at the whole file, so the chapter starts at its body.
    def files_toc(lead):
        files, toc = _prefixed(1, lead)
        toc[1] = NavPoint("Chapter Two", "text/ch02.xhtml")
        return files, toc

    bare = _epub(tmp_path, "bare.epub", *files_toc(_lead(kind)))
    wrapped = _epub(tmp_path, "wrapped.epub",
                    *files_toc(f"<p>{_lead(kind)}</p>"))
    assert _shape(bare) == _shape(wrapped)
    second = bare.chapters[1].markdown
    assert "AFTERLEAD" in second and "BODY2" in second
