import os
import pytest
import yaml
import json as _json


@pytest.fixture
def tmp_vault(tmp_path):
    """Create a temporary vault directory with wiki.yaml and standard structure."""
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
    (vault / "log.md").write_text("# Activity Log\n")

    for topic in config["topics"]:
        (vault / topic).mkdir()

    return vault


@pytest.fixture
def tmp_config(tmp_path, tmp_vault, monkeypatch):
    """Create a temporary user config pointing to tmp_vault."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(yaml.dump({"vault_path": str(tmp_vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return config_file


@pytest.fixture
def tmp_settings(tmp_path):
    """Create an empty settings.json file and return its path.

    Used to test `awiki hook install --config-path <path>`.
    """
    settings_path = tmp_path / "claude-settings.json"
    settings_path.write_text(_json.dumps({}))
    return settings_path
