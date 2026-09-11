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


# --- multi-vault index rebuild -------------------------------------------------

def test_index_multi_vault_sections(two_vault_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    result = CliRunner().invoke(cli, ["index"])
    assert result.exit_code == 0, result.output
    assert "vault: work" in result.output
    assert "vault: personal" in result.output
    assert result.output.count("Index rebuilt.") == 2


def test_index_multi_vault_skips_unreachable_with_notice(tmp_path, monkeypatch):
    import yaml
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    from conftest import make_vault

    work = make_vault(tmp_path / "work-vault")
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({
        "vaults": {
            "work": {"path": str(work)},
            "team": {"url": "http://127.0.0.1:9", "token": None},
        }
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    result = CliRunner().invoke(cli, ["index"])
    assert result.exit_code == 0, result.output
    assert "vault: team" in result.output
    assert "skipped" in result.output
    assert "Index rebuilt." in result.output
