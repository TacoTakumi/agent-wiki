from agent_wiki.server_config import (
    hash_token, role_for_token, role_rank, load_server_config, DEFAULT_PORT,
)


def test_hash_is_sha256_hex():
    h = hash_token("secret")
    assert len(h) == 64 and h == hash_token("secret")


def test_role_lookup():
    cfg = {"tokens": [{"name": "a", "role": "writer", "hash": hash_token("tok")}]}
    assert role_for_token("tok", cfg) == "writer"
    assert role_for_token("nope", cfg) is None


def test_role_rank_order():
    assert role_rank("admin") > role_rank("writer") > role_rank("reader")


def test_load_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    cfg = load_server_config()
    assert cfg["port"] == DEFAULT_PORT and cfg["tokens"] == []


def test_add_list_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    from agent_wiki.server_config import (
        add_token, list_tokens, revoke_token, role_for_token, load_server_config,
    )
    secret = add_token("laptop", "admin")
    assert isinstance(secret, str) and len(secret) >= 32
    assert role_for_token(secret, load_server_config()) == "admin"
    assert [t["name"] for t in list_tokens()] == ["laptop"]
    assert "hash" not in list_tokens()[0]  # never expose the hash
    assert revoke_token("laptop") is True
    assert list_tokens() == []
    assert revoke_token("laptop") is False
