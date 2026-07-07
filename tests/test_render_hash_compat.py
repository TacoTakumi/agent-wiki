"""REQ-11 regression lock: render_hash is additive, optional frontmatter.

A shared vault routinely holds a *mix* of pages — some written by current awiki
(carrying render_hash) and some by older awiki or foreign tools (no render_hash).
No read command (show / search / lint / status) may require the field or let its
presence perturb output for un-hashed pages. These tests would fail if any read
path started reading render_hash or rendering it into user-visible output.

Green by construction: render_hash was designed additive (body-only, in
frontmatter, self-excluding) and no read path was changed to consult it. This
file locks that in so a future change cannot quietly break mixed-vault reads.
"""
from pathlib import Path

from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.ingest import ingest_file
from agent_wiki.page import parse_page, update_frontmatter


def _ingest(vault: Path, tmp_path: Path, name: str, body: str) -> Path:
    """Ingest a page (current awiki stamps render_hash on the write)."""
    src = tmp_path / f"{name}.md"
    src.write_text(f"# {name}\n\n{body}\n")
    return ingest_file(src, vault, topic="research")


def _strip_render_hash(page: Path) -> None:
    """Turn a freshly-ingested page into an older-awiki-shaped one: drop the
    render_hash frontmatter field, leaving the body byte-identical."""
    parsed = parse_page(page)
    meta = parsed["meta"]
    meta.pop("render_hash", None)
    update_frontmatter(page, meta)


def _rel(vault: Path, page: Path) -> str:
    return page.relative_to(vault).as_posix()


def test_show_unhashed_page_is_verbatim_and_hashless(tmp_config, tmp_vault, tmp_path):
    page = _ingest(tmp_vault, tmp_path, "legacy", "a unique legacy body token")
    _strip_render_hash(page)

    result = CliRunner().invoke(cli, ["show", _rel(tmp_vault, page)])
    assert result.exit_code == 0, result.output
    # show is verbatim: its stdout is the on-disk bytes, and an un-hashed page has
    # no render_hash to leak. (The resolved read location goes to stderr, REQ-13.)
    assert result.stdout == page.read_text()
    assert "render_hash" not in result.stdout


def test_render_hash_is_inert_to_read_paths(tmp_config, tmp_vault, tmp_path):
    # Read one page four ways as un-hashed, then stamp render_hash and read again.
    # Every read is identical except `show`, which is verbatim and so gains exactly
    # the render_hash line — proving no read path's behaviour depends on the field.
    page = _ingest(tmp_vault, tmp_path, "mix", "parity token widget alpha")
    _strip_render_hash(page)
    rel = _rel(tmp_vault, page)
    runner = CliRunner()

    def reads() -> dict:
        return {
            "show": runner.invoke(cli, ["show", rel]),
            "search": runner.invoke(cli, ["search", "parity token widget"]),
            "lint": runner.invoke(cli, ["lint"]),
            "status": runner.invoke(cli, ["status"]),
        }

    before = reads()
    for name, r in before.items():
        assert r.exit_code == 0, (name, r.output)

    # Stamp render_hash -> the page is now "hashed" (same value ingest would write).
    from agent_wiki.doctor import RenderHashUnstamped
    RenderHashUnstamped().fix(tmp_vault)
    assert parse_page(page)["meta"].get("render_hash")

    after = reads()
    for name, r in after.items():
        assert r.exit_code == 0, (name, r.output)

    # search / lint / status: stdout unchanged by the added frontmatter field.
    # (Compare stdout, not merged output: show now writes a read location to
    # stderr per REQ-13 — orthogonal to whether render_hash perturbs content.)
    assert after["search"].stdout == before["search"].stdout
    assert after["lint"].stdout == before["lint"].stdout
    assert after["status"].stdout == before["status"].stdout

    # show is verbatim: the ONLY difference in stdout is the added render_hash line.
    assert "render_hash" not in before["show"].stdout
    assert "render_hash" in after["show"].stdout
    after_wo_hash = "".join(
        ln for ln in after["show"].stdout.splitlines(keepends=True)
        if not ln.lstrip().startswith("render_hash:")
    )
    assert after_wo_hash == before["show"].stdout


def test_mixed_vault_all_reads_exit_zero(tmp_config, tmp_vault, tmp_path):
    hashed = _ingest(tmp_vault, tmp_path, "modern", "modern hashed body content")
    unhashed = _ingest(tmp_vault, tmp_path, "legacy", "legacy unhashed body content")
    _strip_render_hash(unhashed)
    assert parse_page(hashed)["meta"].get("render_hash")
    assert "render_hash" not in parse_page(unhashed)["meta"]

    runner = CliRunner()
    for args in (
        ["show", _rel(tmp_vault, hashed)],
        ["show", _rel(tmp_vault, unhashed)],
        ["search", "body content"],
        ["lint"],
        ["status"],
    ):
        r = runner.invoke(cli, args)
        assert r.exit_code == 0, (args, r.output)

    # The un-hashed page still shows verbatim with no render_hash injected...
    shown = runner.invoke(cli, ["show", _rel(tmp_vault, unhashed)])
    assert "render_hash" not in shown.output
    # ...and status counts both pages regardless of which carries a hash.
    st = runner.invoke(cli, ["status"])
    assert "research: 2 pages" in st.output
