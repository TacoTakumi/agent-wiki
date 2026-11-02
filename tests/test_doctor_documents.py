"""Doctor on vaults holding imported documents.

The parent page of a multi-chapter document lists its archived original
(raw/assets/<doc>.<ext>) as its source. That file is the input of a
conversion, not the text of the page body, so doctor must never compare the
page with it, report it as drift, or rewrite it.
"""
from pathlib import Path

from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.doctor import (
    RawContentDrift,
    RenderHashDivergent,
    RenderHashUnstamped,
    _page_raw_status,
    run_checks,
)
from agent_wiki.documents import ingest_document
from agent_wiki.ingest import ingest_file
from agent_wiki.page import parse_page, render_page
from agent_wiki.service import LocalVaultService

from convert_fixtures import build_epub, build_html


def _html_vault(vault: Path, tmp_path: Path):
    """Ingest a multi-chapter HTML document (one h1, four h2 sections)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source = build_html(src_dir / "sample.html")
    result = ingest_document(source, vault, topic="research")
    assert result.chapters > 1
    archive = vault / "raw" / "assets" / f"{result.page.stem}.html"
    assert archive.is_file()
    return result, archive


def _epub_vault(vault: Path, tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source = build_epub(src_dir / "book.epub")
    result = ingest_document(source, vault, topic="research")
    assert result.chapters > 1
    archive = vault / "raw" / "assets" / f"{result.page.stem}.epub"
    assert archive.is_file()
    return result, archive


def _drifted_normal_page(vault: Path, tmp_path: Path) -> Path:
    src = tmp_path / "d.md"
    src.write_text("# D\n\noriginal\n")
    page = ingest_file(src, vault, topic="research")
    page.write_text(page.read_text().replace("original", "edited by hand"))
    return vault / "raw" / "d.md"


def _check_names(vault: Path) -> set[str]:
    return {f.check.name for f in run_checks(vault)}


def test_html_document_parent_reports_no_raw_content_drift(tmp_vault, tmp_path):
    _html_vault(tmp_vault, tmp_path)
    assert RawContentDrift().detect(tmp_vault) is None
    assert "raw-content-drift" not in _check_names(tmp_vault)


def test_html_document_doctor_cli_reports_no_raw_content_drift(tmp_vault, tmp_config, tmp_path):
    _html_vault(tmp_vault, tmp_path)
    result = CliRunner().invoke(cli, ["doctor", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "raw-content-drift" not in result.output


def test_html_document_parent_raw_status_is_not_drift(tmp_vault, tmp_path):
    result, _ = _html_vault(tmp_vault, tmp_path)
    parent = parse_page(result.page)
    assert _page_raw_status(tmp_vault, parent) != "diverged"


def test_unhashed_html_document_parent_is_not_reported_divergent(tmp_vault, tmp_path):
    # The render_hash migration checks classify a page through its raw status;
    # an un-hashed parent must not be called divergent from its archived original.
    result, archive = _html_vault(tmp_vault, tmp_path)
    page = parse_page(result.page)
    meta = dict(page["meta"])
    meta.pop("render_hash", None)
    result.page.write_text(render_page(meta, page["body"]))
    assert RenderHashDivergent().detect(tmp_vault) is None
    finding = RenderHashUnstamped().detect(tmp_vault)
    assert finding is None or result.page.name not in finding.detail


def test_reconcile_raw_leaves_html_archive_byte_identical(tmp_vault, tmp_path):
    _, archive = _html_vault(tmp_vault, tmp_path)
    before = archive.read_bytes()
    LocalVaultService(tmp_vault).doctor(reconcile_raw=True)
    assert archive.read_bytes() == before


def test_reconcile_raw_cli_leaves_html_archive_byte_identical(tmp_vault, tmp_config, tmp_path):
    _, archive = _html_vault(tmp_vault, tmp_path)
    before = archive.read_bytes()
    result = CliRunner().invoke(cli, ["doctor", "--reconcile-raw", "--fix"])
    assert result.exit_code == 0, result.output
    assert archive.read_bytes() == before


def test_raw_content_drift_fix_never_writes_under_raw_assets(tmp_vault, tmp_path):
    # Calling the check's fix directly must not touch the archived original either.
    _, archive = _html_vault(tmp_vault, tmp_path)
    before = archive.read_bytes()
    RawContentDrift().fix(tmp_vault)
    assert archive.read_bytes() == before


def test_epub_document_doctor_and_reconcile_leave_archive_alone(tmp_vault, tmp_path):
    _, archive = _epub_vault(tmp_vault, tmp_path)
    before = archive.read_bytes()
    assert "raw-content-drift" not in _check_names(tmp_vault)
    LocalVaultService(tmp_vault).doctor(reconcile_raw=True)
    assert archive.read_bytes() == before


def test_normal_page_drift_still_reported_and_reconciled(tmp_vault, tmp_path):
    _, archive = _html_vault(tmp_vault, tmp_path)
    before = archive.read_bytes()
    raw = _drifted_normal_page(tmp_vault, tmp_path)

    finding = RawContentDrift().detect(tmp_vault)
    assert finding is not None
    assert finding.detail.startswith("1 raw file(s)")
    assert "d.md" in finding.detail

    LocalVaultService(tmp_vault).doctor(reconcile_raw=True)
    assert "edited by hand" in raw.read_text()
    assert archive.read_bytes() == before
    assert RawContentDrift().detect(tmp_vault) is None


def test_chapter_page_drift_still_reported_and_reconciled(tmp_vault, tmp_path):
    # A chapter page's source is its own raw/<doc>/NN-slug.md, which is page
    # text: drift there is real and reconcile still rewrites it.
    result, archive = _html_vault(tmp_vault, tmp_path)
    before = archive.read_bytes()
    chapter = sorted((result.page.parent / result.page.stem).glob("*.md"))[0]
    page = parse_page(chapter)
    raw = tmp_vault / page["meta"]["sources"][0]
    chapter.write_text(render_page(page["meta"], page["body"] + "\nedited by hand\n"))

    finding = RawContentDrift().detect(tmp_vault)
    assert finding is not None and raw.name in finding.detail

    LocalVaultService(tmp_vault).doctor(reconcile_raw=True)
    assert "edited by hand" in raw.read_text()
    assert archive.read_bytes() == before
