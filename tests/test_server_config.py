from agent_wiki.server_config import (
    hash_token, load_server_config, DEFAULT_PORT,
)


def test_hash_is_sha256_hex():
    h = hash_token("secret")
    assert len(h) == 64 and h == hash_token("secret")


def test_load_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    cfg = load_server_config()
    assert cfg["port"] == DEFAULT_PORT and cfg["tokens"] == []


def test_old_token_helpers_are_gone():
    import agent_wiki.server_config as server_config
    for name in ("add_token", "list_tokens", "revoke_token", "generate_token",
                 "role_for_token", "role_rank"):
        assert not hasattr(server_config, name)


def test_set_server_tokens_drops_the_key_and_keeps_the_rest(tmp_path, monkeypatch):
    import yaml
    from agent_wiki.server_config import (
        read_server_yaml, server_config_path, set_server_tokens,
    )
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    entry = {"name": "a", "role": "reader", "hash": hash_token("tok")}
    server_config_path().write_text(yaml.dump(
        {"bind": "0.0.0.0", "port": 9000, "tokens": [entry]}))
    set_server_tokens([])
    assert read_server_yaml() == {"bind": "0.0.0.0", "port": 9000}
    set_server_tokens([entry])
    assert read_server_yaml()["tokens"] == [entry]


def test_set_server_tokens_writes_nothing_without_a_file(tmp_path, monkeypatch):
    from agent_wiki.server_config import server_config_path, set_server_tokens
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    set_server_tokens([])
    assert not server_config_path().exists()
