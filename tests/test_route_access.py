"""Every route the app serves names its minimum access in one table, and the
table is checked against the app: an undeclared route fails, a stale entry
fails, and each entry's access is tried for real. FastAPI's own docs pages
are not served, and no route manages users or grants."""
import re

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import (ROUTE_ACCESS, app_routes, create_app,
                                   undeclared_routes)
from agent_wiki.server.store import open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from conftest import sign_in, signed_in_client, signin_form

SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}
ACCESS_LEVELS = {"public", "viewer", "reader", "writer", "admin"}
# One role below each API role: a token with it must be turned away.
ROLE_BELOW = {"writer": "reader-tok", "admin": "writer-tok"}
ROLE_TOKEN = {"reader": "reader-tok", "writer": "writer-tok",
              "admin": "admin-tok"}
# A value for each path parameter, so a declared path can be requested.
SAMPLE_PARAMS = {"name": "main", "topic": "research", "tag": "sample",
                 "path": "research/sample.md"}


def _viewer_app(vault):
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=vault)})
    return create_app(vault, SERVER_CONFIG, viewer=catalog)


@pytest.fixture
def viewer_app(tmp_vault, server_tokens, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    return _viewer_app(tmp_vault)


def _url(path):
    return re.sub(r"\{(\w+)(?::\w+)?\}",
                  lambda m: SAMPLE_PARAMS[m.group(1)], path)


def _call(client, method, path, **kwargs):
    return client.request(method, _url(path), follow_redirects=False, **kwargs)


# --- the table matches the app ---------------------------------------------------

def test_every_declared_access_is_a_known_level():
    assert set(ROUTE_ACCESS.values()) <= ACCESS_LEVELS


def test_every_route_is_declared(server_app):
    # Both with and without the viewer: the API alone has no undeclared route.
    assert undeclared_routes(server_app) == []


def test_the_table_names_no_route_the_app_lacks(viewer_app):
    # With the viewer on, the app serves every route the table names.
    assert set(ROUTE_ACCESS) - app_routes(viewer_app) == set()


def test_without_the_viewer_only_api_routes_are_served(tmp_vault, server_tokens):
    app = create_app(tmp_vault, SERVER_CONFIG)
    assert app_routes(app) == {key for key, access in ROUTE_ACCESS.items()
                               if access not in ("public", "viewer")}


def test_an_undeclared_route_fails_the_check(viewer_app):
    @viewer_app.get("/not-in-the-table")
    def extra():
        return {}

    @viewer_app.post("/v1/extra")
    def extra_api():
        return {}

    assert undeclared_routes(viewer_app) == [
        ("GET", "/not-in-the-table"), ("POST", "/v1/extra")]


def test_an_undeclared_route_in_an_included_router_fails_the_check(viewer_app):
    from fastapi import APIRouter
    router = APIRouter(prefix="/more")

    @router.get("/thing")
    def thing():
        return {}

    viewer_app.include_router(router, prefix="/outer")
    assert undeclared_routes(viewer_app) == [("GET", "/outer/more/thing")]


def test_create_app_refuses_an_undeclared_route(tmp_vault, server_tokens,
                                                monkeypatch):
    from agent_wiki.server import app as app_module
    real = app_module.build_router

    def with_extra(svc, require):
        router = real(svc, require)
        # The router adds its own /v1 prefix.
        router.add_api_route("/extra", lambda: {}, methods=["GET"])
        return router

    monkeypatch.setattr(app_module, "build_router", with_extra)
    with pytest.raises(RuntimeError, match="GET /v1/extra"):
        create_app(tmp_vault, SERVER_CONFIG)


# --- each declaration holds -------------------------------------------------------

def _declared(*levels):
    return sorted(key for key, access in ROUTE_ACCESS.items() if access in levels)


@pytest.mark.parametrize("method, path", _declared("public"))
def test_a_public_route_answers_without_credentials(viewer_app, method, path):
    client = TestClient(viewer_app, raise_server_exceptions=False)
    # The sign-in post is public but carries its form's token, as the form
    # a browser was served does.
    data = signin_form(client) if (method, path) == ("POST", "/signin") else None
    resp = _call(client, method, path, data=data)
    assert resp.status_code < 500
    assert resp.status_code not in (401, 403)
    assert "/signin" not in resp.headers.get("location", "")


@pytest.mark.parametrize("method, path", _declared("viewer"))
def test_a_viewer_route_sends_no_session_to_sign_in(viewer_app, method, path):
    client = TestClient(viewer_app, raise_server_exceptions=False)
    resp = _call(client, method, path)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/signin")


@pytest.mark.parametrize("method, path", _declared("viewer"))
def test_a_viewer_route_takes_no_bearer_token(viewer_app, admin_h, method, path):
    client = TestClient(viewer_app, raise_server_exceptions=False)
    resp = _call(client, method, path, headers=admin_h)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/signin")


@pytest.mark.parametrize("method, path", _declared("reader", "writer", "admin"))
def test_an_api_route_answers_401_without_a_token(viewer_app, method, path):
    client = TestClient(viewer_app, raise_server_exceptions=False)
    assert _call(client, method, path).status_code == 401


@pytest.mark.parametrize("method, path", _declared("reader", "writer", "admin"))
def test_an_api_route_takes_no_browser_session(viewer_app, method, path):
    client = signed_in_client(viewer_app, role="admin")
    assert _call(client, method, path).status_code == 401


@pytest.mark.parametrize("method, path", _declared("writer", "admin"))
def test_an_api_route_turns_away_the_role_below(viewer_app, method, path):
    client = TestClient(viewer_app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {ROLE_BELOW[ROUTE_ACCESS[method, path]]}"}
    assert _call(client, method, path, headers=headers).status_code == 403


@pytest.mark.parametrize("method, path", _declared("reader", "writer", "admin"))
def test_an_api_route_lets_its_declared_role_in(viewer_app, method, path):
    # Only the guard is under test: a request it lets through may still be
    # refused for its body or its target, but never for its role.
    client = TestClient(viewer_app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {ROLE_TOKEN[ROUTE_ACCESS[method, path]]}"}
    resp = _call(client, method, path, headers=headers)
    assert resp.status_code not in (401, 403)


# --- FastAPI's docs pages are not served ------------------------------------------

DOCS_PATHS = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_pages_are_not_served_to_a_session(viewer_app, path):
    client = signed_in_client(viewer_app, role="admin")
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 404


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_pages_are_not_served_to_an_admin_token(server_app, admin_h, path):
    client = TestClient(server_app, raise_server_exceptions=False)
    resp = client.get(path, headers=admin_h, follow_redirects=False)
    assert resp.status_code == 404


# --- no route manages users or grants ----------------------------------------------

MANAGING_WORDS = ("users", "grant", "account", "admin", "role", "members")


def test_no_route_path_manages_users_or_grants(viewer_app):
    for method, path in app_routes(viewer_app):
        lowered = path.lower()
        assert not any(word in lowered for word in MANAGING_WORDS), (method, path)


def test_admin_adds_only_doctor_to_writer():
    assert _declared("admin") == [("POST", "/v1/doctor")]


@pytest.mark.parametrize("op", ["add_user", "remove_user", "set_grant",
                                "grant", "revoke_grant", "set_role"])
def test_the_user_page_form_manages_no_user_or_grant(viewer_app, op):
    client = TestClient(viewer_app, raise_server_exceptions=False)
    secret = sign_in(client, role="admin")
    with open_store(viewer_app.state.store_file) as store:
        users_before = [u.name for u in store.list_users()]
        grants_before = {name: store.grants(name) for name in users_before}
    resp = client.post("/user", data={
        "op": op, "session": secret, "name": "intruder", "user": "intruder",
        "vault": "main", "role": "admin"}, follow_redirects=False)
    assert resp.status_code == 400
    with open_store(viewer_app.state.store_file) as store:
        assert [u.name for u in store.list_users()] == users_before
        assert {name: store.grants(name)
                for name in users_before} == grants_before


def test_a_vault_admin_can_run_doctor_on_that_vault(tmp_vault, tmp_path,
                                                    monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    with open_store() as store:
        accounts.add_user(store, "keeper", None)
        accounts.set_grant(store, "keeper", "main", "admin")
        secret = accounts.mint_token(store, "keeper", "cli", None)
        accounts.add_user(store, "editor", None)
        accounts.set_grant(store, "editor", "main", "writer")
        editor = accounts.mint_token(store, "editor", "cli", None)
    client = TestClient(create_app(tmp_vault, SERVER_CONFIG, vault_name="main"),
                        raise_server_exceptions=False)
    keeper_h = {"Authorization": f"Bearer {secret}"}
    resp = client.post("/v1/doctor", json={"dry_run": True}, headers=keeper_h)
    assert resp.status_code == 200, resp.text
    editor_h = {"Authorization": f"Bearer {editor}"}
    assert client.post("/v1/doctor", json={"dry_run": True},
                       headers=editor_h).status_code == 403
    # On another vault the grant on main counts for nothing.
    other = TestClient(create_app(tmp_vault, SERVER_CONFIG, vault_name="other"),
                       raise_server_exceptions=False)
    assert other.post("/v1/doctor", json={"dry_run": True},
                      headers=keeper_h).status_code == 403
