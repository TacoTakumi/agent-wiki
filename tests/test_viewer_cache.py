"""Signed-in viewer pages carry the session secret in their forms, so no
cache may keep them: each is sent with Cache-Control: private, no-store."""
from __future__ import annotations

import pytest

from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.viewer.catalog import build_vault_catalog

from conftest import make_vault, signed_in_client


@pytest.fixture
def client(tmp_path):
    vault = make_vault(tmp_path / "main")
    (vault / "research" / "foo.md").write_text(
        "---\ntitle: Foo\ntopic: research\n---\n\n# Foo\n\nalpha body\n")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=vault)})
    app = create_app(vault, {"bind": "127.0.0.1", "port": 8731},
                     viewer=catalog)
    return signed_in_client(app)


@pytest.mark.parametrize("url", ["/", "/vaults/main/research/foo",
                                 "/search?q=alpha", "/user"])
def test_signed_in_pages_are_not_cached(client, url):
    response = client.get(url)
    assert response.status_code == 200
    cache = response.headers.get("cache-control", "")
    assert "no-store" in cache
    assert "private" in cache


def test_the_sign_in_page_has_no_session_and_no_such_header(tmp_path):
    from fastapi.testclient import TestClient
    vault = make_vault(tmp_path / "main")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=vault)})
    app = create_app(vault, {"bind": "127.0.0.1", "port": 8731},
                     viewer=catalog)
    response = TestClient(app).get("/signin")
    assert "no-store" not in response.headers.get("cache-control", "")


def test_a_refreshed_session_cookie_is_never_cached(tmp_path):
    # A JSON 404 from a viewer route has no Cache-Control of its own; the
    # refreshed cookie carries the secret, so the middleware adds it.
    import time
    from fastapi.testclient import TestClient
    from agent_wiki.server import accounts
    from agent_wiki.server.store import User, open_store
    from agent_wiki.viewer.session import SESSION_COOKIE
    vault = make_vault(tmp_path / "main")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=vault)})
    app = create_app(vault, {"bind": "127.0.0.1", "port": 8731},
                     viewer=catalog)
    with open_store(app.state.store_file) as store:
        store.add_user(User(name="ann", password_hash=None,
                            created="2026-01-01T00:00:00Z"))
        store.set_grant("ann", "*", "reader")
        secret = accounts.open_session(
            store, "ann", "password",
            now=time.time() - accounts.SESSION_WRITE_EVERY - 1)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/vaults/main/research/missing")
    assert response.status_code == 404
    assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE}={secret};")
    assert response.headers["cache-control"] == "private, no-store"
