"""XML comments in EPUB content documents.

The converter cuts content documents at comment markers of its own; a comment
written in the book must never be read as one of those markers, and no
comment text reaches the markdown.
"""
import dataclasses

from convert_fixtures import build_epub, default_epub_content

from agent_wiki.convert.epub import convert_epub


def _book(tmp_path, name, comment=None, chapter=2):
    """The default three-chapter EPUB; ``comment`` is put in the middle of
    chapter ``chapter``'s body (after its opening paragraph)."""
    files, toc = default_epub_content()
    if comment is not None:
        f = files[chapter - 1]
        head, sep, tail = f.body.partition("</p>\n")
        files[chapter - 1] = dataclasses.replace(
            f, body=head + sep + f"<!--{comment}-->\n<p>AFTERCOMMENT</p>\n" + tail)
    return convert_epub(build_epub(tmp_path / name, files=files, toc=toc))


def _shape(doc):
    return [(c.ordinal, c.title) for c in doc.chapters]


def test_out_of_range_marker_comment_converts_with_the_same_chapters(tmp_path):
    plain = _book(tmp_path, "plain.epub")
    forged = _book(tmp_path, "forged.epub", comment="awiki-cut:99")
    assert _shape(forged) == _shape(plain)
    assert "AFTERCOMMENT" in forged.chapters[1].markdown
    for c in forged.chapters:
        assert "awiki-cut" not in c.markdown


def test_in_range_marker_comment_keeps_every_chapter_text_in_its_chapter(
        tmp_path):
    plain = _book(tmp_path, "plain.epub")
    forged = _book(tmp_path, "forged.epub", comment="awiki-cut:0", chapter=2)
    assert _shape(forged) == _shape(plain)
    one, two, three = (c.markdown for c in forged.chapters)
    assert "BODY1" in one and "AFTERCOMMENT" not in one
    assert "BODY2" in two and "AFTERCOMMENT" in two
    assert "Section 2.1" in two
    assert "BODY3" in three and "AFTERCOMMENT" not in three
    # Chapter 2's text is unchanged apart from the paragraph next to the
    # comment.
    assert two.replace("AFTERCOMMENT\n\n", "") == plain.chapters[1].markdown


def test_ordinary_comments_do_not_appear_in_the_markdown(tmp_path):
    doc = _book(tmp_path, "c.epub", comment=" editor note SECRETNOTE ",
                chapter=1)
    text = "\n".join(c.markdown for c in doc.chapters)
    assert "SECRETNOTE" not in text
    assert "<!--" not in text
    assert "AFTERCOMMENT" in doc.chapters[0].markdown
