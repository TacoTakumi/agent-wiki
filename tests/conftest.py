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


@pytest.fixture
def server_tokens():
    from agent_wiki.server_config import hash_token
    return {
        "reader-tok": "reader",
        "writer-tok": "writer",
        "admin-tok": "admin",
    }, [
        {"name": "r", "role": "reader", "hash": hash_token("reader-tok")},
        {"name": "w", "role": "writer", "hash": hash_token("writer-tok")},
        {"name": "a", "role": "admin", "hash": hash_token("admin-tok")},
    ]


@pytest.fixture
def server_app(tmp_vault, server_tokens, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    from agent_wiki.server.app import create_app
    _tokens_map, token_entries = server_tokens
    cfg = {"bind": "127.0.0.1", "port": 8731, "tokens": token_entries}
    return create_app(tmp_vault, cfg)


@pytest.fixture
def client(server_app):
    from fastapi.testclient import TestClient
    return TestClient(server_app, raise_server_exceptions=False)


@pytest.fixture
def reader_h():
    return {"Authorization": "Bearer reader-tok"}


@pytest.fixture
def writer_h():
    return {"Authorization": "Bearer writer-tok"}


@pytest.fixture
def admin_h():
    return {"Authorization": "Bearer admin-tok"}
