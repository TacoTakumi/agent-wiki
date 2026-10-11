"""The viewer and the API name the served vault by one rule, so a grant
means the same thing on both surfaces: a name-matched override keeps its
registry name, a path override is 'local' even when the path is a registered
vault's, and with no override the default vault's registry name is used.

Each app here is built the way serve builds it: the catalog from
load_vault_catalog, the vault from get_vault_path, in an isolated config dir
with a registry of two vaults."""

import pytest
import yaml
from fastapi.testclient import TestClient

from agent_wiki.config import get_vault_path
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import open_store
from agent_wiki.viewer.catalog import (
    load_vault_catalog, served_vault_name,
)
from agent_wiki.viewer.session import SESSION_COOKIE
from conftest import make_vault

SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}
PAGE = ("---\ntitle: Note\ntopic: research\ntags: []\n"
        "created: 2026-01-01\nupdated: 2026-01-02\n---\n\n# Note\n\nHello.\n")


@pytest.fixture
def vaults(tmp_path, monkeypatch):
    """'main' and 'work' in an isolated config dir's registry, each with one
    page at research/note."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("AWIKI_VAULT", raising=False)
    paths = {}
    for name in ("main", "work"):
        vault = make_vault(tmp_path / f"{name}-vault")
        (vault / "research" / "note.md").write_text(PAGE)
        paths[name] = vault
    (config_dir / "config.yaml").write_text(yaml.dump(
        {"vaults": {name: {"path": str(path)} for name, path in paths.items()}}))
    return paths


def _serve_app():
    """The app as serve builds it, with the API name left to create_app."""
    return create_app(get_vault_path(), SERVER_CONFIG,
                      viewer=load_vault_catalog())


def _user(app, name, grants):
    """Add `name` with `grants`; return (API headers, a signed-in client)."""
    with open_store(app.state.store_file) as store:
        accounts.add_user(store, name, None)
        for vault, role in grants.items():
            accounts.set_grant(store, name, vault, role)
        secret = accounts.mint_token(store, name, "t", None)
        session = accounts.open_session(store, name, "password")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(SESSION_COOKIE, session)
    return {"Authorization": f"Bearer {secret}"}, client


def _answers(app, user, grants, vault_name):
    """(API status, viewer status) for one user on the served vault."""
    headers, client = _user(app, user, grants)
    api = client.get("/v1/status", headers=headers).status_code
    viewer = client.get(f"/vaults/{vault_name}/research/note").status_code
    return api, viewer


# --- a path override that is a registered vault's path ----------------------


def test_a_path_override_on_a_registered_vault_is_local_on_both(
        vaults, monkeypatch):
    monkeypatch.setenv("AWIKI_VAULT", str(vaults["main"]))
    assert list(load_vault_catalog().vaults) == ["local"]
    assert served_vault_name() == "local"


@pytest.mark.parametrize("grants, expected", [
    # A wildcard reader with main denied reads the vault as 'local' on both
    # surfaces: no split where one allows and the other refuses.
    ({"*": "reader", "main": "none"}, (200, 200)),
    ({"local": "reader"}, (200, 200)),
    # A grant on the registry name does not reach a vault served as 'local'.
    ({"main": "reader"}, (403, 404)),
])
def test_a_path_override_answers_the_same_on_viewer_and_api(
        vaults, monkeypatch, grants, expected):
    monkeypatch.setenv("AWIKI_VAULT", str(vaults["main"]))
    app = _serve_app()
    assert _answers(app, "ann", grants, "local") == expected


# --- a name override -------------------------------------------------------


def test_a_name_override_keeps_its_registry_name_on_both(vaults, monkeypatch):
    monkeypatch.setenv("AWIKI_VAULT", "main")
    assert list(load_vault_catalog().vaults) == ["main"]
    assert served_vault_name() == "main"


@pytest.mark.parametrize("grants, expected", [
    ({"main": "reader"}, (200, 200)),
    ({"*": "reader", "main": "none"}, (403, 404)),
    ({"local": "reader"}, (403, 404)),
])
def test_a_name_override_answers_the_same_on_viewer_and_api(
        vaults, monkeypatch, grants, expected):
    monkeypatch.setenv("AWIKI_VAULT", "main")
    app = _serve_app()
    assert _answers(app, "ann", grants, "main") == expected


# --- no override -----------------------------------------------------------


def test_no_override_serves_the_default_vault_under_its_registry_name(vaults):
    assert served_vault_name() == "main"
    assert set(load_vault_catalog().vaults) == {"main", "work"}
    app = _serve_app()
    assert _answers(app, "ann", {"main": "reader"}, "main") == (200, 200)
    assert _answers(app, "bob", {"work": "reader"}, "main") == (403, 404)


def test_no_override_follows_default_vault(vaults, tmp_path):
    config_file = tmp_path / "cfg" / "config.yaml"
    config = yaml.safe_load(config_file.read_text())
    config["default_vault"] = "work"
    config_file.write_text(yaml.dump(config))
    assert served_vault_name() == "work"
    app = _serve_app()
    assert _answers(app, "ann", {"work": "reader"}, "work") == (200, 200)
    assert _answers(app, "bob", {"main": "reader"}, "work") == (403, 404)


# --- the rule itself -------------------------------------------------------


def test_served_name_rule(tmp_path):
    from agent_wiki.registry import VaultEntry
    from agent_wiki.viewer.catalog import served_name
    main = VaultEntry(name="main", path=tmp_path / "main")
    work = VaultEntry(name="work", path=tmp_path / "work")
    registry = {"main": main, "work": work}
    same_path = VaultEntry(name=str(main.path), path=main.path)
    assert served_name(registry, main) == "main"
    assert served_name(registry, same_path) == "local"
    assert served_name(registry) == "main"
    assert served_name(registry, None, "work") == "work"
    # No default to give: an empty registry, or several with no 'main'.
    assert served_name({}) == "local"
    assert served_name({"work": work, "x": main}) == "local"


def test_create_app_names_another_path_local_not_the_configured_vault(vaults):
    # The configured vault is 'main'; an app built for 'work' by path must not
    # borrow main's name, or main's grants would open work's API.
    app = create_app(vaults["work"], SERVER_CONFIG)
    headers, _ = _user(app, "ann", {"main": "reader"})
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/v1/status", headers=headers).status_code == 403
    local_headers, _ = _user(app, "bob", {"local": "reader"})
    assert client.get("/v1/status", headers=local_headers).status_code == 200


def test_create_app_names_the_configured_path_by_its_served_name(vaults):
    app = create_app(vaults["main"], SERVER_CONFIG)
    headers, _ = _user(app, "ann", {"main": "reader"})
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/v1/status", headers=headers).status_code == 200
