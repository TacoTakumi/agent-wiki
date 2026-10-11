"""The sign-in form's pre-session token.

GET /signin leaves a short-lived cookie and puts the same value in a hidden
field; POST /signin answers 403 unless the two match, before any password
work. A form on another site can neither read nor set the cookie, so it
cannot sign a browser in to an account of its choosing.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import User, open_store
from agent_wiki.viewer.auth_routes import SIGNIN_COOKIE
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.session import SESSION_COOKIE

from conftest import make_vault, signin_form

USER = "alice"
PASSWORD = "correct horse battery"


@pytest.fixture
def app(tmp_path):
    vault = make_vault(tmp_path / "main")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=vault)})
    app = create_app(vault, {"bind": "127.0.0.1", "port": 8731},
                     viewer=catalog)
    with open_store(app.state.store_file) as store:
        store.add_user(User(name=USER,
                            password_hash=accounts.hash_password(PASSWORD,
                                                                 n=1024),
                            created="2026-01-01T00:00:00Z"))
        store.set_grant(USER, "*", "reader")
    return app


@pytest.fixture
def anon(app):
    return TestClient(app, raise_server_exceptions=False)


def session_cookies(response):
    return [h for h in response.headers.get_list("set-cookie")
            if h.startswith(f"{SESSION_COOKIE}=")]


def stored_state(app):
    with open_store(app.state.store_file) as store:
        sessions = store.connection.execute(
            "SELECT COUNT(*) FROM sessions").fetchone()[0]
        return sessions, store.list_signin_failures(USER)


def test_the_sign_in_page_sets_the_cookie_and_the_matching_field(anon):
    response = anon.get("/signin")
    cookie = next(h for h in response.headers.get_list("set-cookie")
                  if h.startswith(f"{SIGNIN_COOKIE}="))
    value = cookie.split(";")[0].split("=", 1)[1]
    attrs = cookie.lower()
    assert "httponly" in attrs
    assert "samesite=strict" in attrs
    assert "path=/signin" in attrs
    assert f'name="signin" value="{value}"' in response.text


def test_a_matching_token_signs_in(anon):
    data = signin_form(anon)
    data.update(name=USER, password=PASSWORD)
    response = anon.post("/signin", data=data, follow_redirects=False)
    assert response.status_code == 303
    assert session_cookies(response)


@pytest.mark.parametrize("case", ["no cookie", "no field", "mismatch"])
def test_a_post_without_a_matching_token_is_refused_before_any_check(
        app, anon, monkeypatch, case):
    data = signin_form(anon)
    data.update(name=USER, password=PASSWORD)
    if case == "no cookie":
        anon.cookies.delete(SIGNIN_COOKIE, path="/signin")
    elif case == "no field":
        del data["signin"]
    else:
        data["signin"] = "not-the-token"
    calls = []
    monkeypatch.setattr(accounts, "check_password",
                        lambda *a, **k: calls.append(a))
    response = anon.post("/signin", data=data, follow_redirects=False)
    assert response.status_code == 403
    assert not session_cookies(response)
    assert calls == []
    assert stored_state(app) == (0, [])


def test_a_wrong_password_with_a_matching_token_still_counts(app, anon):
    data = signin_form(anon)
    data.update(name=USER, password="wrong")
    response = anon.post("/signin", data=data, follow_redirects=False)
    assert response.status_code == 400
    assert len(stored_state(app)[1]) == 1
