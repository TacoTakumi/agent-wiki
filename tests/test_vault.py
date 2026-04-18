import yaml
import pytest
from pathlib import Path
from agent_wiki.vault import init_vault


def test_init_vault_creates_structure(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    vault_path = tmp_path / "my-wiki"
    init_vault(vault_path)

    assert (vault_path / "wiki.yaml").exists()
    assert (vault_path / "raw").is_dir()
    assert (vault_path / "index.md").exists()
    assert (vault_path / "log.md").exists()
    assert (vault_path / "projects").is_dir()
    assert (vault_path / "decisions").is_dir()
    assert (vault_path / "research").is_dir()
    assert (vault_path / "tools").is_dir()
    assert (vault_path / "sessions").is_dir()
    assert (vault_path / "raw" / "sessions").is_dir()
    assert (vault_path / "incoming").is_dir()

    config = yaml.safe_load((vault_path / "wiki.yaml").read_text())
    assert config["vault"]["name"] == "my-wiki"
    assert config["topics"] == ["projects", "decisions", "research", "tools", "sessions"]
    assert config["conversations"]["topic"] == "sessions"
    assert config["summarizer"]["type"] == "none"
    assert config["sources"]["claude_code"]["enabled"] is True
    assert config["sources"]["drop_zone"]["enabled"] is True


def test_init_vault_sets_user_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    vault_path = tmp_path / "my-wiki"
    init_vault(vault_path)

    user_config = yaml.safe_load((config_dir / "config.yaml").read_text())
    assert user_config["vault_path"] == str(vault_path)


def test_init_vault_already_exists(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    vault_path = tmp_path / "my-wiki"
    init_vault(vault_path)

    with pytest.raises(FileExistsError):
        init_vault(vault_path)
