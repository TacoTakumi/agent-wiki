from agent_wiki import __version__
from agent_wiki.guide import (
    END_MARKER,
    render_block,
    render_guide,
)


def test_render_block_marker_carries_version():
    block = render_block()
    # begin marker now embeds the package version
    assert block.startswith(f"<!-- awiki:begin v{__version__} -->")
    assert block.rstrip().endswith(END_MARKER)
    # canonical content is present
    assert "Knowledge base: the Agent Wiki" in block


def test_begin_marker_embeds_given_version():
    from agent_wiki.guide import BEGIN_PREFIX, begin_marker

    assert begin_marker("9.9.9") == "<!-- awiki:begin v9.9.9 -->"
    # the version-independent anchor agents grep for is a prefix of the marker
    assert begin_marker("9.9.9").startswith(BEGIN_PREFIX)
    assert begin_marker() == f"<!-- awiki:begin v{__version__} -->"


def test_render_block_contains_version_note():
    block = render_block()
    # the visible note carries the version number...
    assert __version__ in block
    # ...and tells the agent how to detect/refresh a stale copy
    assert "awiki --version" in block
    assert "awiki guide" in block
    assert "re-run" in block.lower()


def test_render_block_contains_literal_mechanical_bits():
    block = render_block()
    for literal in ("awiki search", "awiki show", "awiki-save", "awiki-ingest"):
        assert literal in block, f"missing literal: {literal}"


def test_render_guide_default_includes_header_and_block():
    out = render_guide()
    # header markers / intent
    assert "SET UP THE AGENT WIKI" in out
    # the full marked block is contained verbatim
    assert render_block() in out


def test_render_guide_header_instructs_version_readapt():
    out = render_guide()
    lower = out.lower()
    # upgrade path re-adapts (preserving project customizations) rather than
    # blindly replacing — but copies the literal awiki commands verbatim.
    assert "older" in lower       # triggered by an older installed version
    assert "re-adapt" in lower
    assert "preserv" in lower     # preserve project-specific wording
    assert "verbatim" in lower    # literal awiki commands copied as-is
    assert "replace" not in lower  # not a blind wholesale replace


def test_render_guide_raw_omits_header_keeps_block():
    raw = render_guide(raw=True)
    assert "SET UP THE AGENT WIKI" not in raw
    assert raw == render_block()


from click.testing import CliRunner

from agent_wiki.cli import cli


def test_cli_guide_default_runs_without_vault():
    # No AGENT_WIKI_CONFIG_DIR / no vault: must still succeed.
    res = CliRunner().invoke(cli, ["guide"])
    assert res.exit_code == 0
    assert "SET UP THE AGENT WIKI" in res.output
    assert f"<!-- awiki:begin v{__version__} -->" in res.output
    assert END_MARKER in res.output


def test_cli_guide_raw_flag_omits_header():
    res = CliRunner().invoke(cli, ["guide", "--raw"])
    assert res.exit_code == 0
    assert "SET UP THE AGENT WIKI" not in res.output
    assert f"<!-- awiki:begin v{__version__} -->" in res.output
    assert END_MARKER in res.output


def test_cli_directions_alias_still_works():
    # `directions` is a hidden, deprecated alias for `guide`: same output, so
    # any muscle-memory invocation or half-installed block keeps working.
    guide_out = CliRunner().invoke(cli, ["guide"])
    alias_out = CliRunner().invoke(cli, ["directions"])
    assert alias_out.exit_code == 0
    assert alias_out.output == guide_out.output
    # ...but it stays hidden from the top-level help listing.
    help_out = CliRunner().invoke(cli, ["--help"])
    assert "directions" not in help_out.output


def test_cli_version_reports_package_version():
    # The re-sync mechanism compares `awiki --version` against the version
    # stamped in the begin marker (agent_wiki.__version__). They must share one
    # source, or "the version increased" can never be detected.
    res = CliRunner().invoke(cli, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.output


def test_block_describes_shared_vault():
    # The vault is one machine-wide store shared across all projects and the
    # assistant, not a per-project one. The directions must say so.
    lower = render_block().lower()
    assert "shared" in lower
    assert "all your projects" in lower


def test_block_only_references_real_commands():
    # Regression guard: the block tells agents to run these bare `awiki` verbs.
    # Fail the build if any stops being a registered command — this is the
    # root cause of the historical "agents run `awiki show`, it errors" confusion.
    block = render_block()
    for name in ("search", "show", "reingest", "raw"):
        assert f"awiki {name}" in block
        assert name in cli.commands, f"block references awiki {name}, not a real command"


def test_block_is_trimmed_and_habits_inline():
    # The packaged block is short and self-contained: the file itself is <=15
    # non-blank lines, and the load-bearing habits live inline (not deferred to
    # a pointer) — search->show, save, and the edit-raw->reingest edit-loop.
    from importlib import resources

    file_text = (resources.files("agent_wiki") / "data" / "guide.md").read_text(
        encoding="utf-8"
    )
    nonblank = [ln for ln in file_text.splitlines() if ln.strip()]
    assert len(nonblank) <= 15, f"block file has {len(nonblank)} non-blank lines (>15)"

    block = render_block()
    # (a) search-first then read the full page
    assert "awiki search" in block
    assert "awiki show" in block
    # save nudge
    assert "awiki-save" in block
    # (b) exactly one edit-raw -> reingest loop, with a never-hand-edit sense
    assert block.count("awiki reingest") == 1
    assert "hand-edit" in block.lower()


def test_block_drops_web_search_replacement_and_vault_branch():
    # The wiki is framed as first-stop for durable project/domain knowledge, NOT
    # as a general web-search replacement; and the edit-loop is one uniform
    # instruction with no vault-type conditional.
    # collapse whitespace so a line-wrap can't hide the phrase ("searching the\nweb")
    flat = " ".join(render_block().lower().split())
    assert "searching the web" not in flat
    assert "instead of web search" not in flat
    for branch in ("network vault", "remote vault", "local vault"):
        assert branch not in flat
