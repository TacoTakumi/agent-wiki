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
