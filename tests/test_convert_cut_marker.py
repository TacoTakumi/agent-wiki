"""Book text that looks like the converters' internal chapter cut marker.

The EPUB and HTML converters cut a content document at markers of their own.
Text inside a script element, a style element or a CDATA section is written
out raw when the tree is serialized, so a book can hold text that reads
exactly like such a marker. That text must never cut the document: the book
converts without error, with the same chapters as without it, and every
chapter's text stays in its own chapter.
"""
import dataclasses

import pytest
from convert_fixtures import build_epub, default_epub_content

from agent_wiki.convert.epub import convert_epub
from agent_wiki.convert.html import convert_html

# Snippets put in the middle of chapter 2, each forging the marker that
# points at chapter number ``{n}``. The EPUB is XHTML (parsed as XML), so a
# literal "<!--" inside script would be a real comment: it is written as a
# character reference, which the serializer then writes out raw.
EPUB_FORGERIES = {
    "script": "<script>var x = '&lt;!--awiki-cut:{n}--&gt;';</script>",
    "style": "<style>/* &lt;!--awiki-cut:{n}--&gt; */</style>",
    "cdata": "<script><![CDATA[var x = '<!--awiki-cut:{n}-->';]]></script>",
}

# In HTML, script and style hold raw text, so the marker is written as is.
HTML_FORGERIES = {
    "script": "<script>var x = '<!--awiki-cut:{n}-->';</script>",
    "style": "<style>/* <!--awiki-cut:{n}--> */</style>",
    "cdata": "<script>//<![CDATA[\nvar x = '<!--awiki-cut:{n}-->';\n//]]>"
             "</script>",
}

# 99 is past the last chapter; 0 is the first chapter.
NUMBERS = [99, 0]


def _shape(doc):
    return [(c.ordinal, c.title) for c in doc.chapters]


def _assert_own_chapters(doc, plain, markers):
    """Same chapters as ``plain``; chapter i holds markers[i] and the
    AFTERMARK paragraph only when it is chapter 2."""
    assert _shape(doc) == _shape(plain)
    assert len(doc.chapters) == len(markers)
    for i, chapter in enumerate(doc.chapters):
        text = chapter.markdown
        for j, marker in enumerate(markers):
            assert (marker in text) == (i == j), (i, marker)
        assert ("AFTERMARK" in text) == (i == 1), i
        assert "awiki-cut" not in text


# ---------------------------------------------------------------- EPUB


def _epub(tmp_path, name, snippet=None):
    """The default three-chapter EPUB; ``snippet`` and an AFTERMARK paragraph
    go after chapter 2's opening paragraph."""
    files, toc = default_epub_content()
    if snippet is not None:
        f = files[1]
        head, sep, tail = f.body.partition("</p>\n")
        files[1] = dataclasses.replace(
            f, body=head + sep + snippet + "\n<p>AFTERMARK</p>\n" + tail)
    return convert_epub(build_epub(tmp_path / name, files=files, toc=toc))


@pytest.mark.parametrize("n", NUMBERS)
@pytest.mark.parametrize("kind", sorted(EPUB_FORGERIES))
def test_epub_forged_marker_does_not_cut(tmp_path, kind, n):
    plain = _epub(tmp_path, "plain.epub")
    forged = _epub(tmp_path, "forged.epub", EPUB_FORGERIES[kind].format(n=n))
    _assert_own_chapters(forged, plain, ["BODY1", "BODY2", "BODY3"])
    assert "Section 2.1" in forged.chapters[1].markdown


# ---------------------------------------------------------------- HTML


def _html(tmp_path, name, snippet=None):
    """Three h2 chapters; ``snippet`` and an AFTERMARK paragraph go after
    chapter 2's paragraph."""
    extra = "" if snippet is None else snippet + "\n<p>AFTERMARK</p>\n"
    body = (
        "<h2>Chapter One</h2>\n<p>Text one. TEXT1</p>\n"
        f"<h2>Chapter Two</h2>\n<p>Text two. TEXT2</p>\n{extra}"
        "<p>More of chapter two. MORE2</p>\n"
        "<h2>Chapter Three</h2>\n<p>Text three. TEXT3</p>\n"
    )
    path = tmp_path / name
    path.write_text("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
                    "<title>Doc</title></head>\n<body>\n" + body
                    + "</body></html>\n", encoding="utf-8")
    return convert_html(path)


@pytest.mark.parametrize("n", NUMBERS)
@pytest.mark.parametrize("kind", sorted(HTML_FORGERIES))
def test_html_forged_marker_does_not_cut(tmp_path, kind, n):
    plain = _html(tmp_path, "plain.html")
    assert len(plain.chapters) == 3
    forged = _html(tmp_path, "forged.html", HTML_FORGERIES[kind].format(n=n))
    _assert_own_chapters(forged, plain, ["TEXT1", "TEXT2", "TEXT3"])
    assert "MORE2" in forged.chapters[1].markdown
