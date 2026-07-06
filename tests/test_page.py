import hashlib
import re
import pytest
from pathlib import Path
from agent_wiki.page import slugify, parse_page, render_page, extract_wikilinks


def test_slugify_simple():
    assert slugify("Payments Service v3") == "payments-service-v3"


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


from agent_wiki.page import render_hash


def test_render_hash_format_and_value():
    body = "\n# Title\n\nSome content here.\n"
    canonical = page_body_for_raw(body)
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    result = render_hash(body)
    assert result == expected
    # literal prefix + exactly 16 lowercase hex chars
    assert result.startswith("sha256:")
    digest = result[len("sha256:"):]
    assert re.fullmatch(r"[0-9a-f]{16}", digest)


def test_render_hash_stable_under_trailing_whitespace_and_newlines():
    # Bodies differing only in the render_page leading blank line and trailing
    # newlines normalize to the same canonical body, so they hash equal — the
    # property that keeps normalization from producing a false-positive drift.
    a = "# Title\n\nbody"
    b = "\n# Title\n\nbody\n\n\n"
    assert page_body_for_raw(a) == page_body_for_raw(b)  # precondition
    assert render_hash(a) == render_hash(b)


def test_render_hash_differs_on_real_body_change():
    assert render_hash("# T\n\nbody\n") != render_hash("# T\n\nbody edited\n")
