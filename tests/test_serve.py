def test_serve_builds_app(tmp_config):
    from agent_wiki.config import get_vault_path
    from agent_wiki.server_config import load_server_config
    from agent_wiki.server.app import create_app
    from fastapi.testclient import TestClient
    app = create_app(get_vault_path(), load_server_config())
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/v1/status").status_code == 401


# --- one vault per serve instance (T-19, REQ-26) -------------------------------

def test_serve_instances_from_one_config_serve_different_vaults(
        tmp_path, monkeypatch, server_tokens):
    """Vault selection is per-instance via the --vault/AWIKI_VAULT override;
    two apps built from one config dir serve different vaults."""
    import yaml
    from fastapi.testclient import TestClient
    from agent_wiki.config import get_vault_path
    from agent_wiki.server.app import create_app
    from conftest import make_vault

    work = make_vault(tmp_path / "work-vault")
    personal = make_vault(tmp_path / "personal-vault")
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({
        "default_vault": "work",
        "vaults": {"work": {"path": str(work)},
                   "personal": {"path": str(personal)}},
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    _tokens, token_entries = server_tokens
    cfg = {"bind": "127.0.0.1", "port": 8731, "tokens": token_entries}

    # Default vault instance.
    app_default = create_app(get_vault_path(), cfg)
    # Named-vault instance (as `awiki --vault personal serve --port 8732`).
    monkeypatch.setenv("AWIKI_VAULT", "personal")
    app_named = create_app(get_vault_path(), cfg)
    monkeypatch.delenv("AWIKI_VAULT")

    headers = {"Authorization": "Bearer reader-tok"}
    st_default = TestClient(app_default).get("/v1/status", headers=headers).json()
    st_named = TestClient(app_named).get("/v1/status", headers=headers).json()
    assert st_default["vault"] == str(work)
    assert st_named["vault"] == str(personal)


def test_serve_route_set_is_vault_implicit_and_unchanged(tmp_vault):
    """The wire contract: endpoints stay vault-implicit with no vault
    selector, and the route set is pinned to the current release."""
    from agent_wiki.server.app import create_app

    app = create_app(tmp_vault, {"bind": "127.0.0.1", "port": 8731,
                                 "tokens": []})
    paths = sorted(app.openapi()["paths"].keys())
    assert paths == [
        "/v1/adapt", "/v1/context", "/v1/conversations", "/v1/doctor",
        "/v1/index", "/v1/ingest", "/v1/ingest_url", "/v1/lint", "/v1/log",
        "/v1/pages/{path}", "/v1/reingest", "/v1/search", "/v1/status",
        "/v1/sync",
    ]
    assert not any("vault" in p for p in paths)
