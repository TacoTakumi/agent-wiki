"""Tests for the PDF converter and the page-range PDF extractors in fetch."""
import pytest

from convert_fixtures import build_pdf

from agent_wiki.convert.pdf import convert_pdf
from agent_wiki.fetch import pdf_to_markdown

EXTRACTORS = ["pymupdf4llm", "pdfplumber"]


def _markers(markdown):
    return {n for n in range(1, 7) if f"PAGE{n}" in markdown}


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_outline_splits_into_page_ranges(tmp_path, extractor):
    path = build_pdf(tmp_path / "book.pdf")
    doc = convert_pdf(path, extractor=extractor)
    assert doc.title == "A PDF Book"
    assert [c.ordinal for c in doc.chapters] == [1, 2, 3]
    assert [c.title for c in doc.chapters] == [
        "Chapter One", "Chapter Two", "Chapter Three"]
    assert [_markers(c.markdown) for c in doc.chapters] == [
        {1, 2}, {3, 4}, {5, 6}]
    assert all(c.images == [] for c in doc.chapters)
    assert doc.cover is None and doc.assets == {}


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_pages_before_first_outline_entry_join_chapter_one(tmp_path, extractor):
    toc = [[1, "One", 2], [1, "Two", 4], [1, "Three", 6]]
    doc = convert_pdf(build_pdf(tmp_path / "b.pdf", toc=toc), extractor=extractor)
    assert [_markers(c.markdown) for c in doc.chapters] == [
        {1, 2, 3}, {4, 5}, {6}]


def test_outline_without_three_top_entries_uses_deeper_level(tmp_path):
    toc = [[1, "Part", 1], [2, "A", 1], [2, "B", 3], [2, "C", 5]]
    doc = convert_pdf(build_pdf(tmp_path / "b.pdf", toc=toc))
    assert [c.title for c in doc.chapters] == ["A", "B", "C"]
    assert [_markers(c.markdown) for c in doc.chapters] == [
        {1, 2}, {3, 4}, {5, 6}]


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_no_outline_gives_one_chapter(tmp_path, extractor):
    doc = convert_pdf(build_pdf(tmp_path / "b.pdf", outline=False),
                      extractor=extractor)
    assert len(doc.chapters) == 1
    assert doc.chapters[0].ordinal == 1
    assert doc.chapters[0].title == "Chapter One"
    assert _markers(doc.chapters[0].markdown) == {1, 2, 3, 4, 5, 6}


def test_empty_metadata_title_falls_back_to_first_heading(tmp_path):
    doc = convert_pdf(build_pdf(tmp_path / "b.pdf", title=""))
    assert doc.title == "Chapter One"


def test_no_title_and_no_heading_falls_back_to_stem(tmp_path):
    path = build_pdf(tmp_path / "my-report.pdf", title="", toc=[])
    doc = convert_pdf(path)
    assert doc.title == "my-report"
    assert len(doc.chapters) == 1


def test_metadata_author(tmp_path):
    import pymupdf

    path = build_pdf(tmp_path / "b.pdf")
    pdf = pymupdf.open(path)
    pdf.set_metadata({"title": "A PDF Book", "author": "Ann Author"})
    pdf.saveIncr()
    pdf.close()
    assert convert_pdf(path).author == "Ann Author"
    assert convert_pdf(build_pdf(tmp_path / "c.pdf")).author is None


def test_not_a_pdf_raises_value_error(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_text("just some text, not a PDF\n")
    with pytest.raises(ValueError):
        convert_pdf(path)


def test_unknown_extractor_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="pdf_extractor"):
        convert_pdf(build_pdf(tmp_path / "b.pdf"), extractor="nope")


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_pdf_to_markdown_page_range(tmp_path, extractor):
    data = build_pdf(tmp_path / "b.pdf").read_bytes()
    assert _markers(pdf_to_markdown(data, extractor, pages=[2, 3])) == {3, 4}
    assert _markers(pdf_to_markdown(data, extractor)) == {1, 2, 3, 4, 5, 6}
