import pytest
from pathlib import Path
from agent_wiki.search import search_vault
from agent_wiki.page import render_page


def _create_page(vault, topic, slug, title, body, tags=None):
    meta = {
        "title": title,
        "topic": topic,
        "tags": tags or [],
        "created": "2026-04-14",
        "updated": "2026-04-14",
        "sources": [],
    }
    page_path = vault / topic / f"{slug}.md"
    page_path.write_text(render_page(meta, body))
    return page_path


def test_search_finds_match(tmp_vault):
    _create_page(tmp_vault, "research", "python-notes", "Python Notes",
                 "# Python Notes\n\nPython is a great language.\n")
    _create_page(tmp_vault, "tools", "docker-setup", "Docker Setup",
                 "# Docker Setup\n\nHow to install Docker.\n")

    results = search_vault(tmp_vault, "python")
    assert len(results) == 1
    assert results[0]["title"] == "Python Notes"


def test_search_no_match(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Notes",
                 "# Notes\n\nNothing relevant.\n")

    results = search_vault(tmp_vault, "kubernetes")
    assert len(results) == 0


def test_search_filter_by_topic(tmp_vault):
    _create_page(tmp_vault, "research", "python-research", "Python Research",
                 "# Python Research\n\nPython research content.\n")
    _create_page(tmp_vault, "tools", "python-tool", "Python Tool",
                 "# Python Tool\n\nPython tool content.\n")

    results = search_vault(tmp_vault, "python", topic="tools")
    assert len(results) == 1
    assert results[0]["title"] == "Python Tool"


def test_search_returns_matching_lines(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Notes",
                 "# Notes\n\nLine one.\nPython is great.\nLine three.\n")

    results = search_vault(tmp_vault, "python")
    assert len(results) == 1
    assert any("Python is great" in line for line in results[0]["matches"])


from agent_wiki.search import _tokenize


def test_tokenize_splits_lowercases_and_dedups():
    assert _tokenize("Claude Code Hooks") == ["claude", "code", "hooks"]
    assert _tokenize("  beta   alpha ") == ["beta", "alpha"]
    assert _tokenize("alpha alpha beta") == ["alpha", "beta"]  # dedup, order preserved


def test_tokenize_empty_query_returns_empty():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


from pathlib import Path as _Path
from agent_wiki.search import _skip


def test_skip_excludes_raw_index_and_log():
    assert _skip(_Path("raw/source.md")) is True
    assert _skip(_Path("raw/nested/source.md")) is True
    assert _skip(_Path("index.md")) is True
    assert _skip(_Path("log.md")) is True
    assert _skip(_Path("research/index.md")) is True  # name match inside a topic


def test_skip_allows_normal_pages():
    assert _skip(_Path("research/my-notes.md")) is False
    assert _skip(_Path("tools/docker.md")) is False


def test_search_and_matches_terms_across_lines(tmp_vault):
    # All three terms appear, but on different lines → still an "all" match.
    _create_page(tmp_vault, "research", "hooks", "Hooks Page",
                 "# Hooks Page\n\nClaude is great.\nWe use hooks here.\nAlso some code.\n")
    _create_page(tmp_vault, "research", "partial", "Partial Page",
                 "# Partial Page\n\nClaude is mentioned.\nNo other terms.\n")

    results = search_vault(tmp_vault, "claude hooks code")
    by_title = {r["title"]: r for r in results}

    assert by_title["Hooks Page"]["match_kind"] == "all"
    assert by_title["Hooks Page"]["coverage"] == 3
    assert by_title["Hooks Page"]["term_count"] == 3
    assert by_title["Partial Page"]["match_kind"] == "partial"
    assert by_title["Partial Page"]["coverage"] == 1


def test_search_ranks_higher_coverage_first(tmp_vault):
    _create_page(tmp_vault, "research", "three", "Three",
                 "# Three\n\nalpha beta gamma\n")
    _create_page(tmp_vault, "research", "two", "Two",
                 "# Two\n\nalpha beta only\n")

    results = search_vault(tmp_vault, "alpha beta gamma")
    assert results[0]["title"] == "Three"
    assert results[0]["coverage"] == 3
    assert results[1]["title"] == "Two"
    assert results[1]["coverage"] == 2


def test_search_token_order_and_whitespace_independent(tmp_vault):
    _create_page(tmp_vault, "research", "x", "X", "# X\n\nbeta then alpha\n")
    r1 = search_vault(tmp_vault, "alpha beta")
    r2 = search_vault(tmp_vault, "  beta   alpha ")
    assert [r["path"] for r in r1] == [r["path"] for r in r2]
    assert r1[0]["coverage"] == 2


def test_search_single_token_is_all_tier(tmp_vault):
    _create_page(tmp_vault, "research", "p", "Python Notes",
                 "# Python Notes\n\nPython is a great language.\n")
    results = search_vault(tmp_vault, "python")
    assert len(results) == 1
    assert results[0]["match_kind"] == "all"
    assert results[0]["coverage"] == 1
    assert results[0]["term_count"] == 1


def test_search_python_fallback_multiword(tmp_vault, monkeypatch):
    # Force the Python backend by hiding ripgrep.
    monkeypatch.setattr("agent_wiki.search.shutil.which", lambda name: None)
    _create_page(tmp_vault, "research", "hooks", "Hooks Page",
                 "# Hooks Page\n\nClaude is great.\nWe use hooks.\n")

    results = search_vault(tmp_vault, "claude hooks")
    assert len(results) == 1
    assert results[0]["match_kind"] == "all"
    assert results[0]["coverage"] == 2


def test_search_python_fallback_partial_or(tmp_vault, monkeypatch):
    # Regression: the old Python fallback re.escape'd the '|' and broke OR.
    monkeypatch.setattr("agent_wiki.search.shutil.which", lambda name: None)
    _create_page(tmp_vault, "research", "p", "P", "# P\n\nonly alpha here\n")

    results = search_vault(tmp_vault, "alpha zzz")
    assert len(results) == 1
    assert results[0]["match_kind"] == "partial"
    assert results[0]["coverage"] == 1
