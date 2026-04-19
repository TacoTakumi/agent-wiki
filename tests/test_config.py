import os
import yaml
import pytest
from pathlib import Path
from agent_wiki.config import get_config_dir, load_user_config, save_user_config, load_vault_config, auto_context_enabled


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


def test_auto_context_enabled_defaults_true_when_key_missing(tmp_vault):
    # tmp_vault's wiki.yaml has no auto_context key
    assert auto_context_enabled(tmp_vault) is True


def test_auto_context_enabled_reads_false_from_wiki_yaml(tmp_vault):
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    config["auto_context"] = False
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))
    assert auto_context_enabled(tmp_vault) is False


def test_auto_context_enabled_env_overrides_yaml(tmp_vault, monkeypatch):
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    config["auto_context"] = True
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))
    monkeypatch.setenv("AWIKI_AUTO_CONTEXT", "0")
    assert auto_context_enabled(tmp_vault) is False


def test_auto_context_enabled_env_truthy_values(tmp_vault, monkeypatch):
    for val in ("1", "true", "True", "yes", "on"):
        monkeypatch.setenv("AWIKI_AUTO_CONTEXT", val)
        assert auto_context_enabled(tmp_vault) is True


def test_auto_context_enabled_env_falsy_values(tmp_vault, monkeypatch):
    for val in ("0", "false", "False", "no", "off"):
        monkeypatch.setenv("AWIKI_AUTO_CONTEXT", val)
        assert auto_context_enabled(tmp_vault) is False


def test_auto_context_enabled_returns_false_when_no_wiki_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("AWIKI_AUTO_CONTEXT", raising=False)
    # Empty dir — no wiki.yaml.
    assert auto_context_enabled(tmp_path) is False
