"""Pre-flight guards of the document writer: a title that slugs to a reserved
raw/ folder name, image members that would be written outside the document's
assets folder or are not images, a hand-edited parent page on update or
reingest, and a conversion with no chapters. Every refusal leaves the vault
tree unchanged."""
from pathlib import Path

import pytest
from click.testing import CliRunner
from convert_fixtures import (
    NavPoint, XhtmlFile, build_epub, chapter_body, default_epub_content,
)

from agent_wiki.cli import cli
from agent_wiki.convert.model import Chapter, ConvertedDocument
from agent_wiki.documents import ingest_document, write_document
from agent_wiki.ingest import PageDriftError
from agent_wiki.page import parse_page, update_frontmatter
from agent_wiki.service import LocalVaultService


def _tree(vault: Path) -> dict:
    return {str(p.relative_to(vault)): p.read_bytes()
            for p in sorted(vault.rglob("*")) if p.is_file()}


def _hand_edit(page: Path) -> None:
    page.write_text(page.read_text() + "\nA NOTE ADDED BY HAND\n")


def _book(tmp_path: Path, folder: str, title: str = "A Book",
          n_chapters: int = 3) -> Path:
    files, toc = default_epub_content(n_chapters)
    return build_epub(tmp_path / folder / "book.epub", title=title,
                      files=files, toc=toc)


# --- reserved raw/ folder names ------------------------------------------------


@pytest.mark.parametrize("title,reserved", [("Sessions", "sessions"),
                                            ("Assets", "assets")])
def test_reserved_doc_name_is_remapped_and_never_touches_the_folder(
        tmp_vault, tmp_path, title, reserved):
    folder = tmp_vault / "raw" / reserved
    folder.mkdir(parents=True, exist_ok=True)
    bundle = folder / "0123-abcd.md"
    bundle.write_text("# A conversation bundle\n\nturns\n")
    keep = bundle.read_bytes()
    name = f"{reserved}-document"

    result = ingest_document(_book(tmp_path, "v1", title), tmp_vault)

    assert result.page == tmp_vault / "research" / f"{name}.md"
    assert result.chapters == 3
    assert bundle.read_bytes() == keep
    assert sorted(p.name for p in folder.glob("*.md")) == ["0123-abcd.md"]
    assert (tmp_vault / "raw" / "assets" / f"{name}.epub").is_file()
    assert (tmp_vault / "raw" / "assets" / name / "OEBPS/images/a.jpg").is_file()
    assert len(list((tmp_vault / "raw" / name).glob("*.md"))) == 3
    parent = parse_page(result.page)["meta"]
    assert parent["document"] == name
    assert parent["sources"] == [f"raw/assets/{name}.epub"]
    chapter = parse_page(tmp_vault / "research" / name / "01-chapter-one.md")["meta"]
    assert chapter["document"] == name
    assert chapter["sources"] == [f"raw/{name}/01-chapter-one.md"]

    updated = ingest_document(_book(tmp_path, "v2", title, n_chapters=2),
                              tmp_vault, update=True)

    assert updated.chapters == 2
    assert bundle.read_bytes() == keep
    assert sorted(p.name for p in folder.glob("*.md")) == ["0123-abcd.md"]
    assert not (tmp_vault / "raw" / name / "03-chapter-three.md").exists()
    assert not (tmp_vault / "research" / name / "03-chapter-three.md").exists()


def test_reserved_doc_name_parent_reingest_finds_the_remapped_set(tmp_vault, tmp_path):
    bundle = tmp_vault / "raw" / "sessions" / "0123-abcd.md"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("bundle\n")
    svc = LocalVaultService(tmp_vault)
    svc.ingest(_book(tmp_path, "v1", "Sessions"))
    chapter = tmp_vault / "research" / "sessions-document" / "02-chapter-two.md"
    chapter.unlink()

    out = svc.reingest("sessions-document")

    assert out["page"] == "research/sessions-document.md"
    assert chapter.is_file()
    assert bundle.read_text() == "bundle\n"


# --- image member paths ------------------------------------------------------------


def _doc_with_member(member: str, as_cover: bool = False) -> ConvertedDocument:
    if as_cover:
        return ConvertedDocument(
            title="Evil", cover=member, assets={member: b"img"},
            chapters=[Chapter(1, "One", "one"), Chapter(2, "Two", "two")])
    return ConvertedDocument(
        title="Evil", assets={member: b"img"},
        chapters=[Chapter(1, "One", f"![a]({member})", [member]),
                  Chapter(2, "Two", "two")])


BAD_MEMBERS = [
    "C:/Users/you/evil.png",           # drive-absolute
    "c:evil.png",                       # drive-relative
    "//server/share/evil.png",          # UNC
    "\\\\server\\share\\evil.png",      # UNC, backslashes
    "OEBPS\\..\\..\\evil.png",          # backslash
    "OEBPS/images\\a.png",              # backslash
    "OEBPS/images/evil.bat",            # not an image
    "OEBPS/images/noext",               # no extension
]


@pytest.mark.parametrize("as_cover", [False, True])
@pytest.mark.parametrize("member", BAD_MEMBERS)
def test_unsafe_image_member_is_refused_before_any_write(
        tmp_vault, tmp_path, member, as_cover):
    src = tmp_path / "evil.epub"
    src.write_bytes(b"zip")
    before = _tree(tmp_vault)
    with pytest.raises(ValueError, match="image"):
        write_document(_doc_with_member(member, as_cover), src, tmp_vault)
    assert _tree(tmp_vault) == before


def test_unsafe_member_of_a_single_chapter_document_is_refused(tmp_vault, tmp_path):
    member = "C:/Users/you/evil.png"
    doc = ConvertedDocument(title="Evil", assets={member: b"img"},
                            chapters=[Chapter(1, "One", f"![a]({member})", [member])])
    src = tmp_path / "evil.epub"
    src.write_bytes(b"zip")
    before = _tree(tmp_vault)
    with pytest.raises(ValueError):
        write_document(doc, src, tmp_vault)
    assert _tree(tmp_vault) == before


def _epub_referencing(tmp_path: Path, member: str) -> Path:
    """An EPUB whose chapter 1 (in OEBPS/text/) shows the manifest item that
    normalises to the archive path ``member``."""
    if member.startswith("OEBPS/"):
        href, extra = member[len("OEBPS/"):], None
    else:
        href, extra = "../" + member, {member: b"img"}
    files = [XhtmlFile("text/ch01.xhtml",
                       chapter_body(1, images=["../" + href]), "Chapter One"),
             XhtmlFile("text/ch02.xhtml", chapter_body(2), "Chapter Two")]
    toc = [NavPoint("Chapter One", "text/ch01.xhtml#c1"),
           NavPoint("Chapter Two", "text/ch02.xhtml#c2")]
    return build_epub(tmp_path / "src" / "evil.epub", files=files, toc=toc,
                      cover=None, images={href: b"img"}, extra_members=extra)


@pytest.mark.parametrize("member", [
    "C:/Users/you/evil.bat",
    "C:/Users/you/evil.png",
    "c:evil.png",
    "OEBPS/images\\..\\..\\evil.png",
    "OEBPS/images/evil.txt",
])
def test_epub_with_an_unsafe_image_member_imports_without_it(tmp_vault, tmp_path, member):
    # The converter skips a member the writer would refuse, so the book
    # imports without that image.
    result = ingest_document(_epub_referencing(tmp_path, member), tmp_vault)
    assert result.chapters == 2
    assets = tmp_vault / "raw" / "assets" / "a-book"
    assert not assets.exists() or not any(p.is_file() for p in assets.rglob("*"))
    assert "BODY1" in (tmp_vault / "research" / "a-book" / "01-chapter-one.md").read_text()


def test_member_resolving_outside_the_assets_folder_is_refused(tmp_vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assets = tmp_vault / "raw" / "assets" / "a-book"
    assets.mkdir(parents=True)
    (assets / "OEBPS").symlink_to(outside, target_is_directory=True)
    before = _tree(tmp_vault)
    with pytest.raises(ValueError, match="image"):
        ingest_document(_book(tmp_path, "v1"), tmp_vault)
    assert _tree(tmp_vault) == before
    assert list(outside.rglob("*")) == []


# --- the parent page in the drift guard --------------------------------------------


def test_update_over_hand_edited_parent_is_refused_with_a_diff(tmp_vault, tmp_path):
    ingest_document(_book(tmp_path, "v1"), tmp_vault)
    parent = tmp_vault / "research" / "a-book.md"
    _hand_edit(parent)
    before = _tree(tmp_vault)
    with pytest.raises(PageDriftError) as err:
        ingest_document(_book(tmp_path, "v2", n_chapters=2), tmp_vault, update=True)
    assert "research/a-book.md" in str(err.value)
    assert "--force" in str(err.value)
    assert "-A NOTE ADDED BY HAND" in err.value.diff
    assert "--- page:research/a-book.md" in err.value.diff
    assert "-- [[Chapter Three (A Book)]]" in err.value.diff
    assert _tree(tmp_vault) == before

    ingest_document(_book(tmp_path, "v2", n_chapters=2), tmp_vault,
                    update=True, force=True)
    assert "A NOTE ADDED BY HAND" not in parent.read_text()


def test_update_to_one_chapter_over_hand_edited_parent_is_refused(tmp_vault, tmp_path):
    ingest_document(_book(tmp_path, "v1"), tmp_vault)
    _hand_edit(tmp_vault / "research" / "a-book.md")
    before = _tree(tmp_vault)
    with pytest.raises(PageDriftError):
        ingest_document(_book(tmp_path, "v2", n_chapters=1), tmp_vault, update=True)
    assert _tree(tmp_vault) == before


def test_reingest_parent_over_hand_edited_parent_is_refused_then_forced(
        tmp_vault, tmp_path):
    svc = LocalVaultService(tmp_vault)
    svc.ingest(_book(tmp_path, "v1"))
    parent = tmp_vault / "research" / "a-book.md"
    _hand_edit(parent)
    before = _tree(tmp_vault)
    with pytest.raises(PageDriftError) as err:
        svc.reingest("a-book")
    assert "-A NOTE ADDED BY HAND" in err.value.diff
    assert _tree(tmp_vault) == before
    svc.reingest("a-book", force=True)
    assert "A NOTE ADDED BY HAND" not in parent.read_text()


def test_cli_update_over_hand_edited_parent_prints_the_diff(
        tmp_config, tmp_vault, tmp_path):
    assert CliRunner().invoke(cli, ["ingest", str(_book(tmp_path, "v1"))]).exit_code == 0
    _hand_edit(tmp_vault / "research" / "a-book.md")
    before = _tree(tmp_vault)
    refused = CliRunner().invoke(cli, ["ingest", "--update",
                                       str(_book(tmp_path, "v2", n_chapters=2))])
    assert refused.exit_code == 1
    assert "--- page:research/a-book.md" in refused.stderr
    assert "-A NOTE ADDED BY HAND" in refused.stderr
    assert _tree(tmp_vault) == before


def test_unstamped_parent_with_no_raw_is_not_guarded(tmp_vault, tmp_path):
    """Like a chapter page with no render_hash and no readable raw, an
    unstamped parent has nothing to compare against and is rewritten."""
    ingest_document(_book(tmp_path, "v1"), tmp_vault)
    parent = tmp_vault / "research" / "a-book.md"
    meta = parse_page(parent)["meta"]
    del meta["render_hash"]
    update_frontmatter(parent, meta)
    _hand_edit(parent)
    result = ingest_document(_book(tmp_path, "v2", n_chapters=2), tmp_vault,
                             update=True)
    assert result.chapters == 2
    assert "A NOTE ADDED BY HAND" not in parent.read_text()


# --- zero chapters ---------------------------------------------------------------


def test_zero_chapter_document_is_refused_before_any_write(tmp_vault, tmp_path):
    src = tmp_path / "empty.epub"
    src.write_bytes(b"zip")
    before = _tree(tmp_vault)
    with pytest.raises(ValueError, match="no content"):
        write_document(ConvertedDocument(title="Empty", chapters=[]), src, tmp_vault)
    assert _tree(tmp_vault) == before


def test_epub_with_no_spine_content_is_refused(tmp_vault, tmp_path):
    src = build_epub(tmp_path / "src" / "book.epub", files=[], toc=[])
    before = _tree(tmp_vault)
    with pytest.raises(ValueError, match="no content"):
        ingest_document(src, tmp_vault)
    assert _tree(tmp_vault) == before


def test_cli_zero_chapter_document_prints_the_error_and_exits_1(
        tmp_config, tmp_vault, tmp_path):
    src = build_epub(tmp_path / "src" / "book.epub", files=[], toc=[])
    before = _tree(tmp_vault)
    result = CliRunner().invoke(cli, ["ingest", str(src)])
    assert result.exit_code == 1
    assert result.stderr.startswith("error: could not ingest book.epub: ")
    assert "no content" in result.stderr
    assert result.stdout == ""
    assert _tree(tmp_vault) == before
