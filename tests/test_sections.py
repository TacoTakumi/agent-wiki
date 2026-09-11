"""Unit tests for the pure heading scanner and frontmatter stripper."""

from agent_wiki.sections import scan_headings, strip_frontmatter


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
