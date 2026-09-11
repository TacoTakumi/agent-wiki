"""Unit tests for the pure markdown slicing primitives."""

from agent_wiki.sections import (
    match_headings,
    scan_headings,
    select_section,
    strip_frontmatter,
)


def test_scan_headings_returns_index_level_text_and_raw_line():
    text = "intro\n# Title\nbody\n### Deep  \n"
    assert scan_headings(text) == [
        (1, 1, "Title", "# Title"),
        (3, 3, "Deep", "### Deep  "),
    ]


def test_scan_headings_skips_backtick_fenced_headings():
    text = "# Real\n```\n# Fake\n```\n## After\n"
    assert [h[2] for h in scan_headings(text)] == ["Real", "After"]


def test_scan_headings_skips_tilde_fenced_headings():
    text = "# Real\n~~~\n# Fake\n~~~\n## After\n"
    assert [h[2] for h in scan_headings(text)] == ["Real", "After"]


def test_fence_closes_only_on_same_char_at_least_as_long():
    text = "````\n# Fake\n```\n### Still fake\n````\n# Real\n"
    assert [h[2] for h in scan_headings(text)] == ["Real"]


def test_tilde_fence_is_not_closed_by_backticks():
    text = "~~~\n# Fake\n```\n~~~\n# Real\n"
    assert [h[2] for h in scan_headings(text)] == ["Real"]


def test_setext_headings_are_not_recognised():
    text = "Title\n=====\nSub\n-----\n"
    assert scan_headings(text) == []


def test_hash_without_space_is_not_a_heading():
    text = "#hash\n#\n####### Seven\n# Real\n"
    assert [h[2] for h in scan_headings(text)] == ["Real"]


def test_indented_heading_up_to_three_spaces_counts():
    text = "   # Indented\n    # Code block\n"
    assert [h[2] for h in scan_headings(text)] == ["Indented"]


def test_unclosed_fence_swallows_the_rest_of_the_page():
    text = "# Real\n```\n# Fake\n## Also fake\n"
    assert [h[2] for h in scan_headings(text)] == ["Real"]


def test_scan_headings_on_empty_text():
    assert scan_headings("") == []


def test_strip_frontmatter_removes_a_leading_yaml_block():
    text = "---\ntitle: Thing\ntags: [a]\n---\n\n# Title\nbody\n"
    assert strip_frontmatter(text) == "# Title\nbody\n"


def test_strip_frontmatter_leaves_text_without_frontmatter_untouched():
    text = "# Title\n\nbody with --- inside\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_ignores_an_unterminated_block():
    text = "---\ntitle: Thing\n\n# Title\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_on_empty_text():
    assert strip_frontmatter("") == ""


SECTIONED = """# Page

Intro line.

## Watermarks - as of 2026-09-01

Watermark preamble.

### Feed A

Feed A text.

### Feed B

Feed B text.

## Sources

Source list.
"""


def test_match_headings_lists_every_match_in_file_order():
    text = "## Check log\n\nx\n\n### Old check log\n\ny\n"
    assert [h[3] for h in match_headings(text, "check log")] == [
        "## Check log",
        "### Old check log",
    ]


def test_match_headings_returns_empty_when_nothing_matches():
    assert match_headings(SECTIONED, "nosuch") == []


def test_select_section_stops_before_the_next_same_level_heading():
    out = select_section(SECTIONED, "watermarks")
    assert out == (
        "## Watermarks - as of 2026-09-01\n"
        "\n"
        "Watermark preamble.\n"
        "\n"
        "### Feed A\n"
        "\n"
        "Feed A text.\n"
        "\n"
        "### Feed B\n"
        "\n"
        "Feed B text.\n"
    )


def test_select_section_runs_to_end_of_file_for_the_last_section():
    assert select_section(SECTIONED, "sources") == "## Sources\n\nSource list.\n"


def test_select_section_stops_before_a_higher_level_heading():
    text = "### Deep\n\nx\n\n## Shallow\n\ny\n"
    assert select_section(text, "deep") == "### Deep\n\nx\n"


def test_select_section_matches_case_insensitively_and_trims_the_query():
    assert select_section(SECTIONED, "  SOURCES  ") == "## Sources\n\nSource list.\n"


def test_select_section_takes_the_first_match_in_file_order():
    text = (
        "## Check log\n\nfirst\n\n## Sources\n\nlist\n\n"
        "### Old check log\n\nsecond\n"
    )
    assert select_section(text, "check log") == "## Check log\n\nfirst\n"


def test_select_section_returns_none_when_no_heading_matches():
    assert select_section(SECTIONED, "nosuch") is None


def test_select_section_ends_with_a_newline_when_the_source_does_not():
    assert select_section("## A\nbody", "a") == "## A\nbody\n"
