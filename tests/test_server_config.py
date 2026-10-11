import ipaddress

import pytest

from agent_wiki.server_config import (
    hash_token, load_server_config, DEFAULT_PORT, ProxyAuth, proxy_auth,
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


# --- proxy sign-in settings ---------------------------------------------------

def test_proxy_defaults_fill_when_keys_are_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    cfg = load_server_config()
    assert cfg["auth_proxy"] is False
    assert cfg["auth_proxy_header"] == "X-WEBAUTH-USER"
    assert cfg["auth_trusted_proxies"] == ["127.0.0.1", "::1"]


def test_proxy_defaults_keep_written_values(tmp_path, monkeypatch):
    import yaml
    from agent_wiki.server_config import server_config_path
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    server_config_path().write_text(yaml.dump({
        "auth_proxy": True, "auth_proxy_header": "X-User",
        "auth_trusted_proxies": ["10.0.0.0/8"]}))
    cfg = load_server_config()
    assert cfg["auth_proxy"] is True
    assert cfg["auth_proxy_header"] == "X-User"
    assert cfg["auth_trusted_proxies"] == ["10.0.0.0/8"]


def test_proxy_auth_off_by_default():
    setting = proxy_auth({})
    assert setting == ProxyAuth()
    assert setting.enabled is False
    assert setting.header == "X-WEBAUTH-USER"
    assert setting.trusted == (ipaddress.ip_network("127.0.0.1"),
                               ipaddress.ip_network("::1"))


def test_proxy_auth_off_ignores_the_header_but_checks_the_list():
    setting = proxy_auth({"auth_proxy": False, "auth_proxy_header": ""})
    assert setting == ProxyAuth()
    with pytest.raises(ValueError, match="auth_trusted_proxies"):
        proxy_auth({"auth_proxy": False, "auth_trusted_proxies": ["*"]})


def test_proxy_auth_off_keeps_the_trusted_list():
    setting = proxy_auth({"auth_proxy": False,
                          "auth_trusted_proxies": ["10.0.0.5"]})
    assert not setting.enabled
    assert setting.trusts("10.0.0.5")
    assert not setting.trusts("10.0.0.6")


def test_proxy_auth_on_reads_header_and_list():
    setting = proxy_auth({"auth_proxy": True, "auth_proxy_header": " X-User ",
                          "auth_trusted_proxies": ["10.1.2.3", "192.168.0.0/16"]})
    assert setting.enabled is True
    assert setting.header == "X-User"
    assert setting.trusted == (ipaddress.ip_network("10.1.2.3/32"),
                               ipaddress.ip_network("192.168.0.0/16"))


def test_proxy_auth_on_uses_defaults_for_missing_keys():
    setting = proxy_auth({"auth_proxy": True})
    assert setting.enabled is True
    assert setting.header == "X-WEBAUTH-USER"
    assert setting.trusted == ProxyAuth().trusted


@pytest.mark.parametrize("trusted, fault", [
    ([], "at least one"),
    (None, "at least one"),
    ("127.0.0.1", "at least one"),
    (["*"], "trusts every address"),
    (["127.0.0.1", " * "], "trusts every address"),
    (["0.0.0.0/0"], "trusts every address"),
    (["::/0"], "trusts every address"),
    (["0.0.0.0/0.0.0.0"], "trusts every address"),
    (["not-an-address"], "is not an address or a CIDR range"),
    (["10.0.0.0/33"], "is not an address or a CIDR range"),
    ([42], "is not an address or a CIDR range"),
])
def test_proxy_auth_refuses_bad_trusted_list(trusted, fault):
    with pytest.raises(ValueError, match=fault) as exc:
        proxy_auth({"auth_proxy": True, "auth_trusted_proxies": trusted})
    assert "auth_trusted_proxies" in str(exc.value)


@pytest.mark.parametrize("header", ["", "   ", None, 5])
def test_proxy_auth_refuses_an_empty_header(header):
    with pytest.raises(ValueError, match="auth_proxy_header must name a header"):
        proxy_auth({"auth_proxy": True, "auth_proxy_header": header})


def test_proxy_auth_refuses_an_underscore_header():
    with pytest.raises(ValueError, match="underscore"):
        proxy_auth({"auth_proxy": True, "auth_proxy_header": "X_WEBAUTH_USER"})


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_proxy_auth_refuses_a_non_bool_switch(value):
    with pytest.raises(ValueError, match="auth_proxy must be true or false"):
        proxy_auth({"auth_proxy": value})


@pytest.mark.parametrize("host, trusted", [
    ("127.0.0.1", True),
    ("::1", True),
    ("::ffff:127.0.0.1", True),
    ("10.0.0.1", False),
    ("::ffff:10.0.0.1", False),
    ("testclient", False),
    ("", False),
    (None, False),
])
def test_proxy_auth_trusts_only_listed_peers(host, trusted):
    assert ProxyAuth().trusts(host) is trusted


def test_proxy_auth_trusts_a_peer_inside_a_range():
    setting = proxy_auth({"auth_proxy": True,
                          "auth_trusted_proxies": ["10.0.0.0/8", "fd00::/8"]})
    assert setting.trusts("10.200.3.4")
    assert setting.trusts("::ffff:10.200.3.4")
    assert setting.trusts("fd12::1")
    assert not setting.trusts("11.0.0.1")
    assert not setting.trusts("127.0.0.1")
