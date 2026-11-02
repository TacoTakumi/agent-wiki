"""Vault writer for converted documents: layout, frontmatter, images, cover,
log entry, and the single-page collapse."""
from datetime import date
from pathlib import Path

import pytest
import yaml

from convert_fixtures import build_epub, build_html, image_bytes

from agent_wiki.convert.model import Chapter, ConvertedDocument
from agent_wiki.documents import ingest_document, write_document
from agent_wiki.ingest import StrictTagError
from agent_wiki.page import (
    load_sidecar, page_raw_diverged, parse_page, render_hash, sha256_bytes,
)

CHAPTERS = ["01-chapter-one", "02-chapter-two", "03-chapter-three"]
TITLES = ["Chapter One", "Chapter Two", "Chapter Three"]


def _tree(vault: Path) -> dict:
    return {str(p.relative_to(vault)): p.read_bytes()
            for p in sorted(vault.rglob("*")) if p.is_file()}


def _log_lines(vault: Path) -> list[str]:
    return [l for l in (vault / "log.md").read_text().splitlines()
            if l.startswith("- **")]


@pytest.fixture
def epub(tmp_path):
    return build_epub(tmp_path / "src" / "book.epub")


# --- multi-chapter layout ----------------------------------------------------


def test_epub_writes_every_layout_path(tmp_vault, epub):
    result = ingest_document(epub, tmp_vault)
    assert result.chapters == 3
    assert result.page == tmp_vault / "research" / "a-book.md"

    asset = tmp_vault / "raw" / "assets" / "a-book.epub"
    assert sha256_bytes(asset.read_bytes()) == sha256_bytes(epub.read_bytes())
    assert (tmp_vault / "research" / "a-book.md").is_file()
    for stem in CHAPTERS:
        raw = tmp_vault / "raw" / "a-book" / f"{stem}.md"
        assert raw.is_file()
        side = load_sidecar(raw)
        assert side["fetcher"] == "local"
        assert side["source"] == str(epub)
        assert side["sha256"] == sha256_bytes(raw.read_bytes())
        assert (tmp_vault / "research" / "a-book" / f"{stem}.md").is_file()


def test_parent_page_frontmatter_and_ordered_links(tmp_vault, epub):
    ingest_document(epub, tmp_vault)
    page = parse_page(tmp_vault / "research" / "a-book.md")
    meta = page["meta"]
    assert meta["title"] == "A Book"
    assert meta["document"] == "a-book"
    assert meta["sources"] == ["raw/assets/a-book.epub"]
    assert meta["author"] == "Jane Author"
    assert meta["language"] == "en"
    assert meta["publisher"] == "Fixture Press"
    lines = [l for l in page["body"].splitlines() if l.strip()]
    assert lines[1:] == [f"- [[{t} (A Book)]]" for t in TITLES]


def test_parent_omits_metadata_the_format_lacks(tmp_vault, tmp_path):
    src = build_epub(tmp_path / "bare.epub", author=None, publisher=None,
                     language=None)
    ingest_document(src, tmp_vault)
    meta = parse_page(tmp_vault / "research" / "a-book.md")["meta"]
    assert not {"author", "language", "publisher"} & set(meta)


def test_chapter_page_frontmatter(tmp_vault, epub):
    ingest_document(epub, tmp_vault)
    today = date.today().isoformat()
    for n, (stem, title) in enumerate(zip(CHAPTERS, TITLES), start=1):
        meta = parse_page(tmp_vault / "research" / "a-book" / f"{stem}.md")["meta"]
        assert meta["title"] == f"{title} (A Book)"
        assert meta["document"] == "a-book"
        assert meta["chapter"] == n and isinstance(meta["chapter"], int)
        assert meta["topic"] == "research"
        assert meta["tags"] == []
        assert meta["created"] == today and meta["updated"] == today
        assert meta["sources"] == [f"raw/a-book/{stem}.md"]


def test_topic_and_tags_apply_to_parent_and_every_chapter(tmp_vault, epub):
    result = ingest_document(epub, tmp_vault, topic="tools", tags=["book", "x"])
    assert result.page == tmp_vault / "tools" / "a-book.md"
    pages = [tmp_vault / "tools" / "a-book.md"] + [
        tmp_vault / "tools" / "a-book" / f"{s}.md" for s in CHAPTERS]
    for p in pages:
        meta = parse_page(p)["meta"]
        assert meta["topic"] == "tools"
        assert meta["tags"] == ["book", "x"]
    assert not (tmp_vault / "research" / "a-book.md").exists()


def test_chapter_raw_equals_page_body_and_page_is_stamped(tmp_vault, epub):
    ingest_document(epub, tmp_vault)
    for stem in CHAPTERS + [None]:
        p = (tmp_vault / "research" / "a-book" / f"{stem}.md" if stem
             else tmp_vault / "research" / "a-book.md")
        page = parse_page(p)
        assert page["meta"]["render_hash"] == render_hash(page["body"])
        if stem:
            raw = (tmp_vault / "raw" / "a-book" / f"{stem}.md").read_text()
            assert not page_raw_diverged(page["body"], raw)


# --- images and cover --------------------------------------------------------


def test_referenced_image_extracted_with_page_relative_ref(tmp_vault, epub):
    ingest_document(epub, tmp_vault)
    img = tmp_vault / "raw" / "assets" / "a-book" / "OEBPS" / "images" / "a.jpg"
    assert sha256_bytes(img.read_bytes()) == sha256_bytes(image_bytes("jpg", 120))
    assert not (tmp_vault / "raw" / "assets" / "a-book" / "OEBPS" / "images"
                / "b.png").exists()
    page_path = tmp_vault / "research" / "a-book" / "01-chapter-one.md"
    ref = "../../raw/assets/a-book/OEBPS/images/a.jpg"
    assert f"![figure 1]({ref})" in page_path.read_text()
    assert (page_path.parent / ref).resolve() == img.resolve()


@pytest.mark.parametrize("cover", ["epub2", "epub3"])
def test_cover_line_is_first_in_parent_body(tmp_vault, tmp_path, cover):
    src = build_epub(tmp_path / f"{cover}.epub", cover=cover)
    ingest_document(src, tmp_vault)
    parent = tmp_vault / "research" / "a-book.md"
    first = next(l for l in parse_page(parent)["body"].splitlines() if l.strip())
    ref = "../raw/assets/a-book/OEBPS/images/cover.jpg"
    assert first == f"![cover]({ref})"
    assert (parent.parent / ref).is_file()


def test_no_cover_means_body_is_only_the_links(tmp_vault, tmp_path):
    ingest_document(build_epub(tmp_path / "nc.epub", cover=None), tmp_vault)
    body = parse_page(tmp_vault / "research" / "a-book.md")["body"]
    assert [l for l in body.splitlines() if l.strip()] == [
        f"- [[{t} (A Book)]]" for t in TITLES]


def test_image_member_escaping_the_asset_folder_is_refused(tmp_vault, tmp_path):
    doc = ConvertedDocument(
        title="Evil", assets={"../../x.png": b"png"},
        chapters=[Chapter(1, "One", "![a](../../x.png)", ["../../x.png"]),
                  Chapter(2, "Two", "two")])
    src = tmp_path / "evil.epub"
    src.write_bytes(b"zip")
    before = _tree(tmp_vault)
    with pytest.raises(ValueError):
        write_document(doc, src, tmp_vault)
    assert _tree(tmp_vault) == before


# --- log ---------------------------------------------------------------------


def test_one_log_line_naming_parent_and_chapter_count(tmp_vault, epub):
    before = _log_lines(tmp_vault)
    ingest_document(epub, tmp_vault)
    added = _log_lines(tmp_vault)[len(before):]
    assert len(added) == 1
    assert "research/a-book.md" in added[0]
    assert "(3 chapters)" in added[0]
    assert "ingest: book.epub -> research/a-book.md (3 chapters)" in added[0]


# --- ordinals ----------------------------------------------------------------


def test_ordinals_pad_to_the_chapter_total(tmp_vault, tmp_path):
    doc = ConvertedDocument(
        title="Long", chapters=[Chapter(i, f"C{i}", f"text {i}")
                                for i in range(1, 121)])
    src = tmp_path / "long.pdf"
    src.write_bytes(b"%PDF")
    result = write_document(doc, src, tmp_vault)
    assert result.chapters == 120
    assert (tmp_vault / "raw" / "long" / "001-c1.md").is_file()
    assert (tmp_vault / "raw" / "long" / "120-c120.md").is_file()
    meta = parse_page(tmp_vault / "research" / "long" / "001-c1.md")["meta"]
    assert meta["chapter"] == 1
    assert (tmp_vault / "raw" / "assets" / "long.pdf").read_bytes() == b"%PDF"


# --- tags --------------------------------------------------------------------


def _set_vocab(vault, mode="warn"):
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    config["tags"] = {"mode": mode, "vocabulary": {"stt": ["asr"]}}
    (vault / "wiki.yaml").write_text(yaml.dump(config))


def test_tags_canonicalized_once_for_every_page(tmp_vault, epub, capsys):
    _set_vocab(tmp_vault)
    ingest_document(epub, tmp_vault, tags=["asr"])
    for p in (tmp_vault / "research").rglob("*.md"):
        assert parse_page(p)["meta"]["tags"] == ["stt"]
    assert capsys.readouterr().out.count("canonicalized") == 1


def test_strict_novel_tag_writes_nothing(tmp_vault, epub):
    _set_vocab(tmp_vault, mode="strict")
    before = _tree(tmp_vault)
    with pytest.raises(StrictTagError):
        ingest_document(epub, tmp_vault, tags=["novel"])
    assert _tree(tmp_vault) == before


def test_tag_mode_override_applies(tmp_vault, epub):
    _set_vocab(tmp_vault, mode="warn")
    with pytest.raises(StrictTagError):
        ingest_document(epub, tmp_vault, tags=["novel"], tag_mode="strict")


# --- pre-flight refusals -----------------------------------------------------


def test_existing_archive_is_refused_and_vault_unchanged(tmp_vault, epub):
    ingest_document(epub, tmp_vault)
    before = _tree(tmp_vault)
    with pytest.raises(FileExistsError, match="--update"):
        ingest_document(epub, tmp_vault)
    assert _tree(tmp_vault) == before


def test_unconvertible_file_raises_value_error_and_writes_nothing(tmp_vault, tmp_path):
    bad = tmp_path / "x.epub"
    bad.write_text("not a zip")
    before = _tree(tmp_vault)
    with pytest.raises(ValueError):
        ingest_document(bad, tmp_vault)
    assert _tree(tmp_vault) == before


def test_no_topic_and_no_default_topic_is_refused(tmp_vault, epub):
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    del config["default_topic"]
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))
    before = _tree(tmp_vault)
    with pytest.raises(ValueError, match="no topic"):
        ingest_document(epub, tmp_vault)
    assert _tree(tmp_vault) == before


# --- single-chapter collapse -------------------------------------------------


def test_one_chapter_html_is_a_single_page(tmp_vault, tmp_path):
    src = build_html(tmp_path / "one.html", sections=[(1, "Only Heading", "Body.")],
                     nav=False, image=None)
    before = set(_tree(tmp_vault))
    result = ingest_document(src, tmp_vault)
    assert result.chapters == 1
    assert result.page == tmp_vault / "research" / "sample-document.md"
    added = set(_tree(tmp_vault)) - before
    assert added == {
        "raw/sample-document.md", "raw/sample-document.meta.yaml",
        "raw/assets/sample-document.html", "research/sample-document.md",
    }
    assert not (tmp_vault / "research" / "sample-document").exists()
    assert not (tmp_vault / "raw" / "sample-document").exists()
    meta = parse_page(result.page)["meta"]
    assert meta["title"] == "Sample Document"
    assert meta["sources"] == ["raw/sample-document.md"]
    assert not {"document", "chapter", "source_url"} & set(meta)
    side = load_sidecar(tmp_vault / "raw" / "sample-document.md")
    assert side["fetcher"] == "local"
    assert side["source"] == str(src)
    assert (tmp_vault / "raw" / "assets" / "sample-document.html").read_bytes() \
        == src.read_bytes()
    lines = _log_lines(tmp_vault)
    assert "chapters" not in lines[-1]


def test_single_page_image_ref_is_relative_to_the_page(tmp_vault, tmp_path):
    src = build_html(tmp_path / "one.html", sections=[(1, "Only Heading", "Body.")])
    result = ingest_document(src, tmp_vault, topic="tools", tags=["x"])
    img = tmp_vault / "raw" / "assets" / "sample-document" / "images" / "pic.png"
    assert img.read_bytes() == (tmp_path / "images" / "pic.png").read_bytes()
    ref = "../raw/assets/sample-document/images/pic.png"
    page = parse_page(result.page)
    assert f"![picture]({ref})" in page["body"]
    assert (result.page.parent / ref).resolve() == img.resolve()
    assert page["meta"]["topic"] == "tools" and page["meta"]["tags"] == ["x"]
    raw = (tmp_vault / "raw" / "sample-document.md").read_text()
    assert not page_raw_diverged(page["body"], raw)


def test_single_page_keeps_htm_extension_for_the_archive(tmp_vault, tmp_path):
    src = build_html(tmp_path / "one.htm", sections=[(1, "Only", "Body.")],
                     image=None)
    ingest_document(src, tmp_vault)
    assert (tmp_vault / "raw" / "assets" / "sample-document.htm").is_file()


def test_single_page_with_existing_archive_is_refused(tmp_vault, tmp_path):
    src = build_html(tmp_path / "one.html", sections=[(1, "Only", "Body.")],
                     image=None)
    ingest_document(src, tmp_vault)
    before = _tree(tmp_vault)
    with pytest.raises(FileExistsError):
        ingest_document(src, tmp_vault)
    assert _tree(tmp_vault) == before
