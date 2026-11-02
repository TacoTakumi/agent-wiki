"""EPUB images the document writer will not extract are skipped by the
converter, so the book still imports: a figure or cover whose member has an
uncommon extension (.tif, .bmp, .jpe, none) or an unsafe path (drive,
backslash) is left out of the chapter's images and of ``assets`` and its
``<img>`` keeps its original ``src``. jpg/png figures of the same book are
still extracted. The writer's own member check stays as the backstop when a
converted document is passed to ``write_document`` directly."""
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from convert_fixtures import (
    NavPoint, XhtmlFile, build_epub, chapter_body, default_epub_content,
    image_bytes,
)

from agent_wiki.cli import cli
from agent_wiki.convert.epub import convert_epub
from agent_wiki.convert.model import Chapter, ConvertedDocument
from agent_wiki.documents import write_document

UNCOMMON = ["images/fig.tif", "images/fig.bmp", "images/fig.jpe", "images/fig"]


def _assets(vault: Path) -> list[str]:
    root = vault / "raw" / "assets" / "a-book"
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_file())


def _book_with_figure(tmp_path: Path, member: str) -> Path:
    """Chapter 1 shows images/a.jpg, ``member`` and images/b.png."""
    figures = ["images/a.jpg", member, "images/b.png"]
    files, toc = default_epub_content(image_chapters={1: figures})
    images = {"images/a.jpg": image_bytes("jpg", 120),
              member: image_bytes("png", 50),
              "images/b.png": image_bytes("png", 60)}
    return build_epub(tmp_path / "src" / "book.epub", files=files, toc=toc,
                      cover=None, images=images)


def _rename_cover(epub: Path, new: str) -> Path:
    """Rewrite ``epub`` so its cover member images/cover.jpg is ``new``."""
    with zipfile.ZipFile(epub) as zf:
        members = [(i, zf.read(i)) for i in zf.infolist()]
    with zipfile.ZipFile(epub, "w") as zf:
        for info, data in members:
            name = info.filename
            if name == "OEBPS/images/cover.jpg":
                name = f"OEBPS/{new}"
            elif name.endswith(".opf"):
                data = data.replace(b"images/cover.jpg", new.encode())
            zf.writestr(name, data, compress_type=info.compress_type)
    return epub


@pytest.mark.parametrize("member", UNCOMMON)
def test_converter_skips_an_uncommon_figure_and_keeps_its_src(tmp_path, member):
    doc = convert_epub(_book_with_figure(tmp_path, member))
    first = doc.chapters[0]
    assert first.images == ["OEBPS/images/a.jpg", "OEBPS/images/b.png"]
    assert f"OEBPS/{member}" not in doc.assets
    assert sorted(doc.assets) == ["OEBPS/images/a.jpg", "OEBPS/images/b.png"]
    assert f"![figure 2](../{member})" in first.markdown
    assert "BODY1" in first.markdown


@pytest.mark.parametrize("member", UNCOMMON)
def test_cli_ingests_a_book_with_an_uncommon_figure(
        tmp_config, tmp_vault, tmp_path, member):
    result = CliRunner().invoke(cli, ["ingest",
                                      str(_book_with_figure(tmp_path, member))])
    assert result.exit_code == 0, result.output
    assert "(3 chapters)" in result.stdout
    assert _assets(tmp_vault) == ["OEBPS/images/a.jpg", "OEBPS/images/b.png"]
    chapter = (tmp_vault / "research" / "a-book" / "01-chapter-one.md").read_text()
    assert "BODY1" in chapter
    assert f"../{member})" in chapter
    assert "../../raw/assets/a-book/OEBPS/images/a.jpg" in chapter


@pytest.mark.parametrize("cover", ["images/cover.tif", "images/cover.bmp",
                                   "images/cover.jpe", "images/cover"])
def test_cli_ingests_a_book_with_an_uncommon_cover(
        tmp_config, tmp_vault, tmp_path, cover):
    src = _rename_cover(build_epub(tmp_path / "src" / "book.epub"), cover)
    assert convert_epub(src).cover is None
    result = CliRunner().invoke(cli, ["ingest", str(src)])
    assert result.exit_code == 0, result.output
    assert _assets(tmp_vault) == ["OEBPS/images/a.jpg"]
    parent = (tmp_vault / "research" / "a-book.md").read_text()
    assert "![cover]" not in parent
    assert "BODY2" in (tmp_vault / "research" / "a-book" /
                       "02-chapter-two.md").read_text()


def test_common_cover_is_still_extracted(tmp_config, tmp_vault, tmp_path):
    src = build_epub(tmp_path / "src" / "book.epub")
    assert convert_epub(src).cover == "OEBPS/images/cover.jpg"
    assert CliRunner().invoke(cli, ["ingest", str(src)]).exit_code == 0
    assert _assets(tmp_vault) == ["OEBPS/images/a.jpg", "OEBPS/images/cover.jpg"]


@pytest.mark.parametrize("member", [
    "C:/Users/you/evil.png",
    "c:evil.png",
    "OEBPS/images\\..\\..\\evil.png",
])
def test_unsafe_figure_member_is_skipped_and_the_book_imports(
        tmp_config, tmp_vault, tmp_path, member):
    if member.startswith("OEBPS/"):
        href, extra = member[len("OEBPS/"):], None
    else:
        href, extra = "../" + member, {member: b"img"}
    files = [XhtmlFile("text/ch01.xhtml",
                       chapter_body(1, images=["../images/a.jpg", "../" + href]),
                       "Chapter One"),
             XhtmlFile("text/ch02.xhtml", chapter_body(2), "Chapter Two")]
    toc = [NavPoint("Chapter One", "text/ch01.xhtml#c1"),
           NavPoint("Chapter Two", "text/ch02.xhtml#c2")]
    src = build_epub(tmp_path / "src" / "book.epub", files=files, toc=toc,
                     cover=None, extra_members=extra,
                     images={"images/a.jpg": image_bytes("jpg", 120),
                             href: b"img"})
    doc = convert_epub(src)
    assert member not in doc.assets
    assert doc.chapters[0].images == ["OEBPS/images/a.jpg"]
    result = CliRunner().invoke(cli, ["ingest", str(src)])
    assert result.exit_code == 0, result.output
    assert _assets(tmp_vault) == ["OEBPS/images/a.jpg"]
    assert "BODY1" in (tmp_vault / "research" / "a-book" /
                       "01-chapter-one.md").read_text()


@pytest.mark.parametrize("member", ["OEBPS/images/fig.tif", "OEBPS/images/fig",
                                    "C:/Users/you/evil.png", "../evil.png"])
@pytest.mark.parametrize("as_cover", [False, True])
def test_writer_still_refuses_the_member_when_called_directly(
        tmp_vault, tmp_path, member, as_cover):
    src = tmp_path / "book.epub"
    src.write_bytes(b"zip")
    chapters = [Chapter(1, "One", f"![a]({member})", [] if as_cover else [member]),
                Chapter(2, "Two", "two")]
    doc = ConvertedDocument(title="A Book", chapters=chapters,
                            cover=member if as_cover else None,
                            assets={member: b"img"})
    with pytest.raises(ValueError, match="unsafe image path"):
        write_document(doc, src, tmp_vault)
    assert not (tmp_vault / "research" / "a-book.md").exists()
