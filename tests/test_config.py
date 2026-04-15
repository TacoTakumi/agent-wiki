import os
import yaml
import pytest
from agent_wiki.config import get_config_dir, load_user_config, save_user_config, load_vault_config


def test_get_config_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_WIKI_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = get_config_dir()
    assert result.name == "agent-wiki"
    assert result.parent.name == ".config"


def test_get_config_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path / "custom"))
    result = get_config_dir()
    assert result == tmp_path / "custom"


def test_load_user_config_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path / "nonexistent"))
    config = load_user_config()
    assert config == {}


def test_save_and_load_user_config(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    save_user_config({"vault_path": "/some/path"})
    config = load_user_config()
    assert config["vault_path"] == "/some/path"


def test_load_vault_config(tmp_vault):
    config = load_vault_config(tmp_vault)
    assert config["vault"]["name"] == "Test Wiki"
    assert "projects" in config["topics"]
    assert config["default_topic"] == "research"


def test_load_vault_config_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_vault_config(tmp_path)
