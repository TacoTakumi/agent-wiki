from click.testing import CliRunner
from agent_wiki.cli import cli
from agent_wiki.page import render_page
import yaml


def _setup_vault(tmp_path, monkeypatch):
    """Set up a vault and config for CLI testing."""
    vault = tmp_path / "vault"
    vault.mkdir()
    config = {
        "vault": {"name": "Test Wiki", "version": 1},
        "topics": ["projects", "decisions", "research", "tools"],
        "default_topic": "research",
    }
    (vault / "wiki.yaml").write_text(yaml.dump(config))
    (vault / "raw").mkdir()
    (vault / "index.md").write_text("# Index\n")
    (vault / "log.md").write_text("# Activity Log\n\n- **2026-04-14 10:00** — ingest: test.md -> research/test.md\n")
    for topic in config["topics"]:
        (vault / topic).mkdir()

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return vault


def test_status_command(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)

    meta = {
        "title": "Test Page", "topic": "research", "tags": [],
        "created": "2026-04-14", "updated": "2026-04-14", "sources": [],
    }
    (vault / "research" / "test.md").write_text(
        render_page(meta, "# Test Page\n\nContent.\n")
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "research" in result.output
    assert "1" in result.output


def test_log_command(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["log"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "test.md" in result.output


def test_log_command_last(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["log", "--last", "1"])
    assert result.exit_code == 0
    assert "test.md" in result.output


def _make_page(vault, slug, title, body):
    meta = {
        "title": title, "topic": "research", "tags": [],
        "created": "2026-05-30", "updated": "2026-05-30", "sources": [],
    }
    (vault / "research" / f"{slug}.md").write_text(render_page(meta, body))


def test_search_shows_partial_tier_with_coverage(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    _make_page(vault, "full", "Full Match", "# Full Match\n\nalpha beta gamma\n")
    _make_page(vault, "part", "Partial Match", "# Partial Match\n\nalpha only\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "alpha beta gamma"])
    assert result.exit_code == 0
    assert "Full Match" in result.output
    assert "Partial matches" in result.output
    assert "Partial Match" in result.output
    assert "(1/3 terms)" in result.output


def test_search_caps_all_tier_and_reports_truncation(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    for i in range(5):
        _make_page(vault, f"p{i}", f"Page {i}", f"# Page {i}\n\nalpha beta\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "alpha beta", "--limit", "2"])
    assert result.exit_code == 0
    assert "Showing 2 of 5 matches" in result.output
    assert "narrow your query" in result.output


def test_search_no_results(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "nonexistentterm"])
    assert result.exit_code == 0
    assert "No results found." in result.output
