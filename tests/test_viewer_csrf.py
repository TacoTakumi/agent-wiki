"""The session field on state-changing viewer forms: sign-out and the theme
switch carry the session secret in a hidden `session` field, and a post whose
field is missing or does not match the caller's session gets 403 and changes
nothing - no session row ends or moves, no cookie is set."""

import re

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.routes import THEME_COOKIE
from agent_wiki.viewer.session import SESSION_COOKIE, csrf_refused
from conftest import make_vault, sign_in


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}


@pytest.fixture
def app(tmp_vault, tmp_path):
    main = make_vault(tmp_path / "main")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=main)})
    return create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)


@pytest.fixture
def signed_in(app):
    """A signed-in client and its session secret."""
    client = TestClient(app, raise_server_exceptions=False)
    return client, sign_in(client)


def session_row(app, secret):
    with open_store(app.state.store_file) as store:
        return store.get_session(accounts.hash_secret(secret))


def other_secret(app):
    """The secret of a second live session, another user's."""
    other = TestClient(app, raise_server_exceptions=False)
    return sign_in(other, user="other")


# Field values that must be refused: None leaves the field out.
def bad_fields(secret):
    return {
        "missing": None,
        "empty": "",
        "wrong": "not-the-secret",
        "truncated": secret[:-1],
        "longer": secret + "x",
        "non-ascii": "séssion☃",
    }


BAD = ["missing", "empty", "wrong", "truncated", "longer", "non-ascii"]

FORMS = {
    "/signout": {},
    "/theme": {"theme": "dark", "next": "/"},
}


def post(client, path, field, extra=None):
    data = dict(FORMS[path])
    if field is not None:
        data["session"] = field
    if extra:
        data.update(extra)
    return client.post(path, data=data, follow_redirects=False)


# ---------- refused posts change nothing ----------

@pytest.mark.parametrize("path", sorted(FORMS))
@pytest.mark.parametrize("kind", BAD)
def test_post_with_a_bad_session_field_is_refused(app, signed_in, path, kind):
    client, secret = signed_in
    before = session_row(app, secret)
    resp = post(client, path, bad_fields(secret)[kind])
    assert resp.status_code == 403
    assert "set-cookie" not in resp.headers
    assert session_row(app, secret) == before
    assert client.cookies.get(SESSION_COOKIE) == secret
    assert client.cookies.get(THEME_COOKIE) is None
    # The session still opens pages.
    assert client.get("/", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("path", sorted(FORMS))
def test_another_sessions_secret_is_refused(app, signed_in, path):
    client, secret = signed_in
    other = other_secret(app)
    before = session_row(app, secret), session_row(app, other)
    resp = post(client, path, other)
    assert resp.status_code == 403
    assert "set-cookie" not in resp.headers
    assert (session_row(app, secret), session_row(app, other)) == before


def test_bad_field_is_refused_before_the_theme_is_read(signed_in):
    client, _ = signed_in
    resp = post(client, "/theme", "wrong", {"theme": "purple"})
    assert resp.status_code == 403
    assert "set-cookie" not in resp.headers


# ---------- the matching field goes through ----------

def test_sign_out_with_the_session_field_ends_the_session(app, signed_in):
    client, secret = signed_in
    resp = post(client, "/signout", secret)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"
    assert session_row(app, secret) is None
    cookies = resp.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith(f'{SESSION_COOKIE}="";')


def test_theme_with_the_session_field_sets_the_cookie(app, signed_in):
    client, secret = signed_in
    before = session_row(app, secret)
    resp = post(client, "/theme", secret)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    cookies = resp.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith(f"{THEME_COOKIE}=dark;")
    assert session_row(app, secret) == before


# ---------- the theme switch needs a session ----------

@pytest.mark.parametrize("field", [None, "", "anything"])
def test_theme_without_a_session_goes_to_sign_in(app, field):
    anon = TestClient(app, raise_server_exceptions=False)
    resp = post(anon, "/theme", field)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"
    assert "set-cookie" not in resp.headers


def test_theme_with_an_unknown_session_cookie_goes_to_sign_in(app):
    anon = TestClient(app, raise_server_exceptions=False)
    anon.cookies.set(SESSION_COOKIE, "made-up")
    resp = post(anon, "/theme", "made-up")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"
    assert "set-cookie" not in resp.headers


# ---------- the forms carry the field ----------

def form(html, css_class):
    found = re.search(rf'<form class="{css_class}"[^>]*>(.*?)</form>', html,
                      re.S)
    assert found, css_class
    return found.group(1)


@pytest.mark.parametrize("css_class", ["signout", "theme"])
def test_signed_in_forms_echo_the_session_secret(signed_in, css_class):
    client, secret = signed_in
    html = client.get("/").text
    assert (f'<input type="hidden" name="session" value="{secret}">'
            in form(html, css_class))


def test_sign_in_page_has_no_theme_switch(app):
    anon = TestClient(app, raise_server_exceptions=False)
    html = anon.get("/signin").text
    assert 'action="/theme"' not in html
    assert 'name="session"' not in html


# ---------- the helper ----------

def bare_request(cookie=None):
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode()))
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": headers, "state": {}})


@pytest.mark.parametrize("field", [None, ""])
def test_helper_refuses_when_there_is_no_session(field):
    resp = csrf_refused(bare_request(), field)
    assert resp is not None
    assert resp.status_code == 403


def test_helper_passes_a_matching_field():
    assert csrf_refused(bare_request("abc123"), "abc123") is None


@pytest.mark.parametrize("field", [None, "", "abc12", "ABC123",
                                   "abc123é"])
def test_helper_refuses_a_field_that_does_not_match(field):
    resp = csrf_refused(bare_request("abc123"), field)
    assert resp is not None
    assert resp.status_code == 403
