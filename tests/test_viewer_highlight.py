"""Tests for the viewer's escaped, single-pass query-term highlighting."""

from markupsafe import Markup

from agent_wiki.viewer.highlight import highlight


def test_marks_each_term_case_insensitively():
    assert highlight("Viewer CSS", "viewer css") == (
        "<mark>Viewer</mark> <mark>CSS</mark>"
    )


def test_returns_markup():
    assert isinstance(highlight("Viewer CSS", "viewer"), Markup)
    assert isinstance(highlight("Viewer CSS", ""), Markup)


def test_term_with_html_special_character_matches_raw_text():
    assert highlight("x a&b y", "a&b") == "x <mark>a&amp;b</mark> y"


def test_longest_term_wins():
    assert highlight("abcd", "ab abc") == "<mark>abc</mark>d"


def test_terms_do_not_match_escaped_entities():
    assert highlight("a & b", "amp") == "a &amp; b"


def test_html_in_text_is_escaped_around_marks():
    assert highlight("<b>x</b>", "x") == "&lt;b&gt;<mark>x</mark>&lt;/b&gt;"


def test_empty_query_returns_escaped_text():
    assert highlight("<i>a</i> & b", "") == "&lt;i&gt;a&lt;/i&gt; &amp; b"


def test_whitespace_query_returns_escaped_text():
    assert highlight("<i>a</i>", "   \t\n ") == "&lt;i&gt;a&lt;/i&gt;"


def test_duplicate_terms_mark_once():
    assert highlight("foo bar foo", "foo FOO foo") == (
        "<mark>foo</mark> bar <mark>foo</mark>"
    )


def test_every_occurrence_is_marked():
    assert highlight("aXaxa", "x") == "a<mark>X</mark>a<mark>x</mark>a"


def test_no_match_returns_escaped_text():
    assert highlight("a < b", "zzz") == "a &lt; b"


def test_regex_metacharacters_in_terms_are_literal():
    assert highlight("a.b axb", "a.b") == "<mark>a.b</mark> axb"
