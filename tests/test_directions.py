from agent_wiki import __version__
from agent_wiki.directions import (
    END_MARKER,
    render_block,
    render_directions,
)


def test_render_block_marker_carries_version():
    block = render_block()
    # begin marker now embeds the package version
    assert block.startswith(f"<!-- awiki:begin v{__version__} -->")
    assert block.rstrip().endswith(END_MARKER)
    # canonical content is present
    assert "Knowledge base: the Agent Wiki" in block


def test_begin_marker_embeds_given_version():
    from agent_wiki.directions import BEGIN_PREFIX, begin_marker

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
    assert "awiki directions" in block
    assert "re-run" in block.lower()


def test_render_block_contains_literal_mechanical_bits():
    block = render_block()
    for literal in ("awiki search", "awiki show", "awiki-save", "awiki-ingest"):
        assert literal in block, f"missing literal: {literal}"


def test_render_directions_default_includes_header_and_block():
    out = render_directions()
    # header markers / intent
    assert "SET UP THE AGENT WIKI" in out
    # the full marked block is contained verbatim
    assert render_block() in out


def test_render_directions_header_instructs_version_upgrade():
    out = render_directions()
    lower = out.lower()
    # the idempotency rule is now a version comparison: replace an older block
    assert "version" in lower
    assert "older" in lower
    assert "replace" in lower


def test_render_directions_raw_omits_header_keeps_block():
    raw = render_directions(raw=True)
    assert "SET UP THE AGENT WIKI" not in raw
    assert raw == render_block()


from click.testing import CliRunner

from agent_wiki.cli import cli


def test_cli_directions_default_runs_without_vault():
    # No AGENT_WIKI_CONFIG_DIR / no vault: must still succeed.
    res = CliRunner().invoke(cli, ["directions"])
    assert res.exit_code == 0
    assert "SET UP THE AGENT WIKI" in res.output
    assert f"<!-- awiki:begin v{__version__} -->" in res.output
    assert END_MARKER in res.output


def test_cli_directions_raw_flag_omits_header():
    res = CliRunner().invoke(cli, ["directions", "--raw"])
    assert res.exit_code == 0
    assert "SET UP THE AGENT WIKI" not in res.output
    assert f"<!-- awiki:begin v{__version__} -->" in res.output
    assert END_MARKER in res.output


def test_block_only_references_real_commands():
    # Regression guard: the block tells agents to run `awiki search` / `awiki show`.
    # Fail the build if either stops being a registered command — this is the
    # root cause of the historical "agents run `awiki show`, it errors" confusion.
    block = render_block()
    for name in ("search", "show"):
        assert f"awiki {name}" in block
        assert name in cli.commands, f"block references awiki {name}, not a real command"
