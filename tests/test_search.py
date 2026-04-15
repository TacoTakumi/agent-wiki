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
