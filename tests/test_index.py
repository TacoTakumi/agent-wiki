import pytest
from agent_wiki.index import rebuild_index
from agent_wiki.page import render_page


def _create_page(vault, topic, slug, title, tags=None, updated="2026-04-14"):
    meta = {
        "title": title,
        "topic": topic,
        "tags": tags or [],
        "created": "2026-04-14",
        "updated": updated,
        "sources": [],
    }
    page_path = vault / topic / f"{slug}.md"
    page_path.write_text(render_page(meta, f"# {title}\n\nContent.\n"))
    return page_path


def test_rebuild_index_groups_by_topic(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Research Notes", tags=["python"])
    _create_page(tmp_vault, "tools", "docker", "Docker", tags=["devops"])

    rebuild_index(tmp_vault)

    index_content = (tmp_vault / "index.md").read_text()
    assert "## research" in index_content.lower() or "## Research" in index_content
    assert "## tools" in index_content.lower() or "## Tools" in index_content
    assert "Research Notes" in index_content
    assert "Docker" in index_content


def test_rebuild_index_empty_vault(tmp_vault):
    rebuild_index(tmp_vault)

    index_content = (tmp_vault / "index.md").read_text()
    assert "# Index" in index_content


def test_rebuild_index_shows_tags(tmp_vault):
    _create_page(tmp_vault, "tools", "docker", "Docker", tags=["devops", "containers"])

    rebuild_index(tmp_vault)

    index_content = (tmp_vault / "index.md").read_text()
    assert "devops" in index_content
    assert "containers" in index_content
