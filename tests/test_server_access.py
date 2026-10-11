"""The /v1 bearer guard: tokens come from the user store, and each route's
minimum role is checked against the token's effective role on the served
vault, read fresh at every request."""
import types

import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from agent_wiki.cli import cli
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import open_store, store_path
from agent_wiki.server_config import hash_token
from agent_wiki.viewer.catalog import served_vault_name
from conftest import make_vault

SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    directory = tmp_path / "cfg"
    directory.mkdir()
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(directory))
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    return directory


@pytest.fixture
def registry_vault(tmp_path, config_dir):
    """A vault registered as 'main' in the config dir's registry."""
    vault = make_vault(tmp_path / "main-vault")
    (config_dir / "config.yaml").write_text(
        yaml.dump({"vaults": {"main": {"path": str(vault)}}}))
    return vault


@pytest.fixture
def bare_vault(tmp_path, config_dir):
    """A vault no registry names."""
    return make_vault(tmp_path / "bare-vault")


def _user(name, grants, scope=None, token="t"):
    """Add ``name`` with ``grants`` and a token; returns its bearer header."""
    with open_store() as store:
        accounts.add_user(store, name, None)
        for vault, role in grants.items():
            accounts.set_grant(store, name, vault, role)
        secret = accounts.mint_token(store, name, token, scope)
    return {"Authorization": f"Bearer {secret}"}


def _client(vault, **kwargs):
    return TestClient(create_app(vault, SERVER_CONFIG, **kwargs),
                      raise_server_exceptions=False)


def _awiki(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return result


# --- per-role minimums ---------------------------------------------------------

@pytest.mark.parametrize("token, status, index, doctor", [
    ("reader-tok", 200, 403, 403),
    ("writer-tok", 200, 200, 403),
    ("admin-tok", 200, 200, 200),
])
def test_each_role_meets_each_route_minimum(client, token, status, index, doctor):
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/status", headers=headers).status_code == status
    assert client.post("/v1/index", headers=headers).status_code == index
    assert client.post("/v1/doctor", json={}, headers=headers).status_code == doctor


def test_effective_none_is_refused_403(bare_vault):
    headers = _user("ann", {"*": "none"})
    r = _client(bare_vault).get("/v1/status", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"] == "insufficient role"


def test_a_user_with_no_grant_is_refused_403(bare_vault):
    headers = _user("ann", {})
    assert _client(bare_vault).get("/v1/status", headers=headers).status_code == 403


def test_a_scope_narrows_the_grant(bare_vault):
    headers = _user("ann", {"*": "admin"}, scope={"*": "reader"})
    c = _client(bare_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    assert c.post("/v1/index", headers=headers).status_code == 403


def test_a_scope_cannot_widen_the_grant(bare_vault):
    headers = _user("ann", {"*": "reader"}, scope={"*": "admin"})
    c = _client(bare_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    assert c.post("/v1/index", headers=headers).status_code == 403


@pytest.mark.parametrize("headers, detail", [
    ({}, "missing bearer token"),
    ({"Authorization": "Basic dXNlcjpwdw=="}, "missing bearer token"),
    ({"Authorization": "Bearer nope"}, "invalid token"),
])
def test_no_or_unknown_token_is_401(client, headers, detail):
    r = client.get("/v1/status", headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"] == detail


# --- the served vault's name -----------------------------------------------------

def test_a_registry_vault_is_served_under_its_registry_name(registry_vault):
    assert served_vault_name() == "main"


def test_a_registry_vault_honours_a_grant_on_its_name(registry_vault):
    headers = _user("ann", {"main": "reader"})
    c = _client(registry_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    assert c.post("/v1/index", headers=headers).status_code == 403


def test_a_registry_vault_refuses_a_grant_on_another_vault(registry_vault):
    headers = _user("ann", {"other": "admin"})
    assert _client(registry_vault).get(
        "/v1/status", headers=headers).status_code == 403


def test_a_bare_path_vault_is_served_as_local(bare_vault, monkeypatch):
    monkeypatch.setenv("AWIKI_VAULT", str(bare_vault))
    assert served_vault_name() == "local"
    headers = _user("ann", {"local": "reader"})
    assert _client(bare_vault).get("/v1/status", headers=headers).status_code == 200


@pytest.mark.parametrize("override, expected", [
    (None, "main"), ("main", "main"), ("path", "local"),
    ("registered-path", "local")])
def test_serve_passes_the_served_vault_name(
        registry_vault, bare_vault, monkeypatch, override, expected):
    import uvicorn
    import agent_wiki.server.app as app_module
    seen = {}
    real_create_app = app_module.create_app

    def recording_create_app(*args, **kwargs):
        seen["vault_name"] = kwargs.get("vault_name")
        return real_create_app(*args, **kwargs)

    monkeypatch.setattr(app_module, "create_app", recording_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    if override == "path":
        monkeypatch.setenv("AWIKI_VAULT", str(bare_vault))
    elif override == "registered-path":
        # A path override is 'local' even when it is a registered vault's
        # path, as the viewer catalog names it.
        monkeypatch.setenv("AWIKI_VAULT", str(registry_vault))
    elif override:
        monkeypatch.setenv("AWIKI_VAULT", override)
    else:
        monkeypatch.delenv("AWIKI_VAULT", raising=False)
    _awiki("serve")
    assert seen["vault_name"] == expected


def test_an_explicit_vault_name_wins(registry_vault):
    headers = _user("ann", {"main": "admin", "work": "none"})
    assert _client(registry_vault, vault_name="work").get(
        "/v1/status", headers=headers).status_code == 403


# --- a CLI change applies at the next request -------------------------------------

def test_a_grant_change_applies_at_the_next_request(bare_vault):
    headers = _user("ann", {"*": "reader"})
    c = _client(bare_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    _awiki("user", "grant", "ann", "*", "none")
    assert c.get("/v1/status", headers=headers).status_code == 403
    _awiki("user", "grant", "ann", "local", "writer")
    assert c.post("/v1/index", headers=headers).status_code == 200


def test_a_revoke_applies_at_the_next_request(bare_vault):
    headers = _user("ann", {"*": "reader"}, token="laptop")
    c = _client(bare_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    _awiki("user", "token", "revoke", "ann", "laptop")
    r = c.get("/v1/status", headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid token"


def test_a_user_remove_applies_at_the_next_request(bare_vault):
    headers = _user("ann", {"*": "admin"})
    c = _client(bare_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    _awiki("user", "remove", "ann")
    assert c.get("/v1/status", headers=headers).status_code == 401


def test_the_store_is_the_one_in_the_config_dir(bare_vault, config_dir):
    _user("ann", {"*": "reader"})
    assert store_path() == config_dir / "state.db"
    assert store_path().is_file()


# --- last use ---------------------------------------------------------------------

def test_last_used_is_written_at_most_once_a_minute(bare_vault, monkeypatch):
    headers = _user("ann", {"*": "reader"})
    clock = [1_800_000_000.0]
    monkeypatch.setattr(accounts, "time", types.SimpleNamespace(time=lambda: clock[0]))
    touches = []
    from agent_wiki.server.store import SqliteStore
    real_touch = SqliteStore.touch_token

    def counting_touch(self, token_hash, when):
        touches.append(when)
        return real_touch(self, token_hash, when)

    monkeypatch.setattr(SqliteStore, "touch_token", counting_touch)
    c = _client(bare_vault)

    def last_used():
        with open_store() as store:
            return accounts.list_tokens(store, "ann")[0].last_used

    assert c.get("/v1/status", headers=headers).status_code == 200
    first = last_used()
    assert first is not None and len(touches) == 1
    clock[0] += 10
    assert c.get("/v1/status", headers=headers).status_code == 200
    assert last_used() == first and len(touches) == 1
    clock[0] += 51
    assert c.get("/v1/status", headers=headers).status_code == 200
    assert last_used() != first and len(touches) == 2


# --- what no bearer token can reach ----------------------------------------------

@pytest.mark.parametrize("viewer", [False, True], ids=["viewer-off", "viewer-on"])
def test_no_api_route_manages_tokens_users_or_grants(tmp_vault, viewer):
    catalog = None
    if viewer:
        from agent_wiki.registry import VaultEntry
        from agent_wiki.viewer.catalog import build_vault_catalog
        catalog = build_vault_catalog({"main": VaultEntry(name="main", path=tmp_vault)})
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    api_paths = [path for path in app.openapi()["paths"] if path.startswith("/v1")]
    assert api_paths
    for path in api_paths:
        assert not any(word in path.lower() for word in ("token", "user", "grant")), path


# --- tokens moved from server.yaml ------------------------------------------------

def test_a_migrated_reader_token_keeps_its_role(bare_vault):
    entry = {"name": "laptop", "role": "reader", "hash": hash_token("old-secret")}
    with open_store() as store:
        accounts.add_user(store, "ops", None)
        accounts.set_grant(store, "ops", "*", "admin")
        imported, skipped = accounts.migrate_server_tokens(store, "ops", [entry])
    assert imported == ["laptop"] and skipped == []
    headers = {"Authorization": "Bearer old-secret"}
    c = _client(bare_vault)
    assert c.get("/v1/status", headers=headers).status_code == 200
    assert c.post("/v1/index", headers=headers).status_code == 403
