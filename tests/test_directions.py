from agent_wiki.directions import (
    BEGIN_MARKER,
    END_MARKER,
    render_block,
    render_directions,
)


def test_render_block_is_wrapped_in_markers():
    block = render_block()
    assert block.startswith(BEGIN_MARKER)
    assert block.rstrip().endswith(END_MARKER)
    # canonical content is present
    assert "Knowledge base: the Agent Wiki" in block


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


def test_render_directions_raw_omits_header_keeps_block():
    raw = render_directions(raw=True)
    assert "SET UP THE AGENT WIKI" not in raw
    assert raw == render_block()
