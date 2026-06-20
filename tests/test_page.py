import pytest
from pathlib import Path
from agent_wiki.page import slugify, parse_page, render_page, extract_wikilinks


def test_slugify_simple():
    assert slugify("ViewPoint API v3") == "viewpoint-api-v3"


def test_slugify_special_chars():
    assert slugify("C++ / Rust Notes") == "c-rust-notes"


def test_slugify_extra_dashes():
    assert slugify("  hello   world  ") == "hello-world"


def test_parse_page_with_frontmatter(tmp_path):
    page_file = tmp_path / "test.md"
    page_file.write_text(
        "---\n"
        "title: Test Page\n"
        "topic: research\n"
        "tags: [python, testing]\n"
        "created: 2026-04-14\n"
        "updated: 2026-04-14\n"
        "sources: []\n"
        "---\n"
        "\n"
        "# Test Page\n"
        "\n"
        "Some content about [[Other Page]] and [[Another]].\n"
    )
    page = parse_page(page_file)
    assert page["meta"]["title"] == "Test Page"
    assert page["meta"]["topic"] == "research"
    assert page["meta"]["tags"] == ["python", "testing"]
    assert "Some content" in page["body"]


def test_parse_page_no_frontmatter(tmp_path):
    page_file = tmp_path / "test.md"
    page_file.write_text("# Just a heading\n\nSome content.\n")
    page = parse_page(page_file)
    assert page["meta"] == {}
    assert "Just a heading" in page["body"]


def test_render_page():
    meta = {
        "title": "Test Page",
        "topic": "research",
        "tags": ["python"],
        "created": "2026-04-14",
        "updated": "2026-04-14",
        "sources": [],
    }
    body = "# Test Page\n\nSome content.\n"
    result = render_page(meta, body)
    assert result.startswith("---\n")
    assert "title: Test Page" in result
    assert "# Test Page" in result


def test_extract_wikilinks():
    text = "Links to [[Page One]] and [[Page Two]] here. Also [[Page One]] again."
    links = extract_wikilinks(text)
    assert links == {"Page One", "Page Two"}


def test_extract_wikilinks_none():
    assert extract_wikilinks("No links here.") == set()


from agent_wiki.page import (
    page_body_for_raw, page_raw_diverged, page_lines_lost, page_raw_diff,
)


def test_page_body_for_raw_strips_leading_blank_and_normalizes_trailing():
    # render_page inserts one leading blank line; raw should not have it.
    assert page_body_for_raw("\n# T\n\nbody\n\n\n") == "# T\n\nbody\n"
    assert page_body_for_raw("# T\n\nbody") == "# T\n\nbody\n"


def test_page_raw_diverged_false_when_equivalent():
    assert page_raw_diverged("\n# T\n\nbody\n", "# T\n\nbody\n") is False


def test_page_raw_diverged_true_when_body_differs():
    assert page_raw_diverged("\n# T\n\nbody plus edit\n", "# T\n\nbody\n") is True


def test_page_lines_lost_counts_diverged_page_lines():
    body = "\n# T\n\nkept\nhand edit one\nhand edit two\n"
    raw = "# T\n\nkept\n"
    assert page_lines_lost(body, raw) == 2


def test_page_raw_diff_marks_page_losses_and_raw_gains():
    body = "\n# T\n\nold line\n"
    raw = "# T\n\nnew line\n"
    diff = page_raw_diff(body, raw, "page:research/t.md", "raw:raw/t.md")
    assert "-old line" in diff
    assert "+new line" in diff
    assert "page:research/t.md" in diff and "raw:raw/t.md" in diff
