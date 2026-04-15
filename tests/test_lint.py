import pytest
from agent_wiki.lint import lint_vault
from agent_wiki.page import render_page


def _create_page(vault, topic, slug, title, body, tags=None, sources=None):
    meta = {
        "title": title,
        "topic": topic,
        "tags": tags or [],
        "created": "2026-04-14",
        "updated": "2026-04-14",
        "sources": sources or [],
    }
    page_path = vault / topic / f"{slug}.md"
    page_path.write_text(render_page(meta, body))
    return page_path


def test_lint_broken_wikilinks(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Notes",
                 "# Notes\n\nSee [[Nonexistent Page]] for details.\n")

    issues = lint_vault(tmp_vault)
    broken = [i for i in issues if i["type"] == "broken_wikilink"]
    assert len(broken) == 1
    assert "Nonexistent Page" in broken[0]["detail"]


def test_lint_valid_wikilinks(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Notes",
                 "# Notes\n\nSee [[Docker]] for details.\n")
    _create_page(tmp_vault, "tools", "docker", "Docker",
                 "# Docker\n\nDocker content.\n")

    issues = lint_vault(tmp_vault)
    broken = [i for i in issues if i["type"] == "broken_wikilink"]
    assert len(broken) == 0


def test_lint_orphan_pages(tmp_vault):
    _create_page(tmp_vault, "research", "orphan", "Orphan Page",
                 "# Orphan Page\n\nNo one links here.\n")

    issues = lint_vault(tmp_vault)
    orphans = [i for i in issues if i["type"] == "orphan"]
    assert len(orphans) == 1


def test_lint_un_ingested_raw(tmp_vault):
    (tmp_vault / "raw" / "stray-file.md").write_text("# Stray\n")

    issues = lint_vault(tmp_vault)
    raw_issues = [i for i in issues if i["type"] == "raw_not_ingested"]
    assert len(raw_issues) == 1
    assert "stray-file.md" in raw_issues[0]["detail"]


def test_lint_missing_frontmatter(tmp_vault):
    page_path = tmp_vault / "research" / "bare.md"
    page_path.write_text("# No Frontmatter\n\nJust content.\n")

    issues = lint_vault(tmp_vault)
    fm_issues = [i for i in issues if i["type"] == "missing_frontmatter"]
    assert len(fm_issues) == 1
