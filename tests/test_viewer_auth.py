"""Viewer sign-in: every page but the sign-in page and the favicons needs a
session; a password sign-in opens one and sets its cookie; refused sign-ins all
read the same and cost one scrypt run each; sign-out ends the session; a
signed-in page shows who is signed in; and the session cookie never opens the
/v1 API."""

import re
import time

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import User, open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.routes import FAVICONS, build_viewer_router
from agent_wiki.viewer.session import SESSION_COOKIE, current_viewer
from conftest import VIEWER_USER, make_vault, sign_in


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}
PASSWORD = "correct horse battery"

PAGE = """---
title: Foo Page
topic: research
tags: [alpha]
---

Body text.
"""

# Every viewer page, as a browser asks for it.
VIEWS = [
    "/",
    "/vaults/main/",
    "/vaults/main/research/",
    "/vaults/main/research/foo",
    "/vaults/main/-/tags/alpha",
    "/search?q=body",
]


@pytest.fixture
def app(tmp_vault, tmp_path):
    main = make_vault(tmp_path / "main")
    (main / "research" / "foo.md").write_text(PAGE)
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=main)})
    return create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)


@pytest.fixture
def anon(app):
    return TestClient(app, raise_server_exceptions=False)


def add_user(app, name, password_hash):
    """Add `name` with `password_hash` (None: proxy-only) straight to the
    app's store, with reader on every vault."""
    with open_store(app.state.store_file) as store:
        store.add_user(User(name=name, password_hash=password_hash,
                            created="2026-01-01T00:00:00Z"))
        store.set_grant(name, "*", "reader")


def stored_hash(app, name):
    with open_store(app.state.store_file) as store:
        return store.get_user(name).password_hash


def session_count(app):
    with open_store(app.state.store_file) as store:
        return store.connection.execute(
            "SELECT COUNT(*) FROM sessions").fetchone()[0]


def post_sign_in(client, name, password, next_=None):
    data = {"name": name, "password": password}
    if next_ is not None:
        data["next"] = next_
    return client.post("/signin", data=data, follow_redirects=False)


def cookie_attributes(header):
    """The attribute names and values of one Set-Cookie header, lowercased."""
    parts = [p.strip() for p in header.split(";")]
    return {p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else "")
            for p in parts[1:]}


def site_header(html):
    found = re.search(r'<header class="site-header">(.*?)</header>', html,
                      re.S)
    assert found, "no site header"
    return found.group(1)


# ---------- no anonymous access ----------

@pytest.mark.parametrize("url", VIEWS)
def test_every_view_without_a_session_redirects_to_sign_in(anon, url):
    resp = anon.get(url, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].split("?")[0] == "/signin"


def test_redirect_carries_the_page_asked_for_as_next(anon):
    resp = anon.get("/vaults/main/research/foo", follow_redirects=False)
    assert resp.headers["location"] == (
        "/signin?next=%2Fvaults%2Fmain%2Fresearch%2Ffoo")
    resp = anon.get("/search?q=body", follow_redirects=False)
    assert resp.headers["location"] == "/signin?next=%2Fsearch%3Fq%3Dbody"


def test_redirect_from_home_carries_no_next(anon):
    assert anon.get("/", follow_redirects=False).headers["location"] == (
        "/signin")


def test_unknown_vault_without_a_session_redirects_not_404(anon):
    resp = anon.get("/vaults/nosuch/", follow_redirects=False)
    assert resp.status_code == 303


def test_sign_in_page_and_favicons_answer_without_a_session(anon):
    assert anon.get("/signin").status_code == 200
    for name in FAVICONS:
        assert anon.get(f"/{name}").status_code == 200


def test_every_page_route_runs_the_session_guard(app):
    # Only the favicons and the theme switch may answer without a session;
    # a page route added later without the guard fails here.
    open_paths = {f"/{name}" for name in FAVICONS} | {"/theme"}
    catalog = build_vault_catalog({})
    for route in build_viewer_router(catalog).routes:
        guards = [d.call for d in route.dependant.dependencies]
        if route.path in open_paths:
            assert current_viewer not in guards, route.path
        else:
            assert current_viewer in guards, route.path


def test_api_answers_401_without_a_token(anon):
    assert anon.get("/v1/status").status_code == 401


def test_session_cookie_alone_does_not_open_the_api(app):
    client = TestClient(app, raise_server_exceptions=False)
    sign_in(client)
    assert client.get("/").status_code == 200
    assert client.get("/v1/status").status_code == 401


# ---------- the sign-in page ----------

def test_sign_in_page_on_an_empty_store_names_user_add(anon):
    html = anon.get("/signin").text
    assert "awiki user add" in html
    assert '<form method="post" action="/signin">' in html


def test_sign_in_page_with_users_does_not_name_user_add(app, anon):
    add_user(app, "ann", None)
    assert "awiki user add" not in anon.get("/signin").text


def test_sign_in_page_shows_no_account_bar(anon):
    header = site_header(anon.get("/signin").text)
    assert "Signed in as" not in header
    assert "/signout" not in header


def test_sign_in_page_keeps_a_same_site_next(anon):
    html = anon.get("/signin?next=/vaults/main/").text
    assert '<input type="hidden" name="next" value="/vaults/main/">' in html
    html = anon.get("/signin?next=//evil.example/").text
    assert '<input type="hidden" name="next" value="/">' in html


def test_sign_in_page_with_a_session_redirects_to_next(app):
    client = TestClient(app, raise_server_exceptions=False)
    sign_in(client)
    resp = client.get("/signin?next=/vaults/main/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/vaults/main/"


# ---------- password sign-in ----------

def test_correct_sign_in_sets_the_session_cookie_and_redirects(app, anon):
    add_user(app, "ann", accounts.hash_password(PASSWORD, n=1024))
    resp = post_sign_in(anon, "Ann", PASSWORD, "/vaults/main/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/vaults/main/"
    header = resp.headers["set-cookie"]
    assert header.startswith(f"{SESSION_COOKIE}=")
    attrs = cookie_attributes(header)
    assert "httponly" in attrs
    assert attrs["samesite"].lower() == "lax"
    assert attrs["path"] == "/"
    assert attrs["max-age"] == str(accounts.SESSION_TTL)
    assert "secure" not in attrs
    # The cookie opens the pages.
    assert anon.get("/vaults/main/").status_code == 200


def test_sign_in_over_https_sets_a_secure_cookie(app):
    add_user(app, "ann", accounts.hash_password(PASSWORD, n=1024))
    client = TestClient(app, base_url="https://testserver",
                        raise_server_exceptions=False)
    resp = post_sign_in(client, "ann", PASSWORD)
    assert resp.status_code == 303
    assert "secure" in cookie_attributes(resp.headers["set-cookie"])


def test_sign_in_to_another_site_goes_home(app, anon):
    add_user(app, "ann", accounts.hash_password(PASSWORD, n=1024))
    resp = post_sign_in(anon, "ann", PASSWORD, "//evil.example/")
    assert resp.headers["location"] == "/"


def test_store_keeps_no_copy_of_the_cookie_value(app, anon):
    add_user(app, "ann", accounts.hash_password(PASSWORD, n=1024))
    resp = post_sign_in(anon, "ann", PASSWORD)
    secret = resp.cookies[SESSION_COOKIE]
    data = app.state.store_file.read_bytes()
    assert secret.encode() not in data
    with open_store(app.state.store_file) as store:
        assert store.get_session(accounts.hash_secret(secret)) is not None


def test_refused_sign_ins_read_the_same_and_cost_one_scrypt_each(
        app, anon, monkeypatch):
    add_user(app, "proxy", None)
    add_user(app, "ann", accounts.hash_password(PASSWORD, n=1024))
    verifies = []
    real_verify = accounts.verify_password

    def counting_verify(password, stored):
        verifies.append(stored)
        return real_verify(password, stored)

    monkeypatch.setattr(accounts, "verify_password", counting_verify)
    bodies = []
    for name in ("nobody", "proxy", "ann"):
        verifies.clear()
        resp = post_sign_in(anon, name, "wrong password", "/vaults/main/")
        assert resp.status_code == 400, name
        assert "set-cookie" not in resp.headers, name
        assert len(verifies) == 1, name
        bodies.append(resp.content)
    assert bodies[0] == bodies[1] == bodies[2]
    assert b"That user name and password do not sign in." in bodies[0]
    assert session_count(app) == 0


def test_old_parameter_hash_is_rehashed_on_sign_in(app, anon):
    add_user(app, "ann", accounts.hash_password(PASSWORD, n=1024))
    assert stored_hash(app, "ann").startswith("scrypt$1024$")
    assert post_sign_in(anon, "ann", PASSWORD).status_code == 303
    new_hash = stored_hash(app, "ann")
    assert new_hash.startswith(f"scrypt${accounts.SCRYPT_N}$")
    assert not accounts.needs_rehash(new_hash)
    assert accounts.verify_password(PASSWORD, new_hash)


def test_wrong_password_leaves_an_old_hash_alone(app, anon):
    old = accounts.hash_password(PASSWORD, n=1024)
    add_user(app, "ann", old)
    assert post_sign_in(anon, "ann", "nope").status_code == 400
    assert stored_hash(app, "ann") == old


# ---------- the signed-in top bar ----------

def test_signed_in_page_shows_the_user_a_user_link_and_sign_out(app):
    client = TestClient(app, raise_server_exceptions=False)
    secret = sign_in(client)
    header = site_header(client.get("/").text)
    assert f'Signed in as <a href="/user">{VIEWER_USER}</a>' in header
    form = re.search(r'<form class="signout" method="post" action="/signout">'
                     r'(.*?)</form>', header, re.S)
    assert form, header
    assert (f'<input type="hidden" name="session" value="{secret}">'
            in form.group(1))


@pytest.mark.parametrize("url", VIEWS)
def test_every_signed_in_view_shows_the_account_bar(app, url):
    client = TestClient(app, raise_server_exceptions=False)
    sign_in(client)
    resp = client.get(url)
    assert resp.status_code == 200
    header = site_header(resp.text)
    assert "Signed in as" in header
    assert 'action="/signout"' in header


# ---------- sign-out ----------

def test_sign_out_ends_the_session_and_clears_the_cookie(app):
    client = TestClient(app, raise_server_exceptions=False)
    secret = sign_in(client)
    resp = client.post("/signout", data={"session": secret},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"
    headers = resp.headers.get_list("set-cookie")
    assert len(headers) == 1
    assert headers[0].startswith(f'{SESSION_COOKIE}="";')
    assert cookie_attributes(headers[0])["max-age"] == "0"
    assert session_count(app) == 0
    # The old cookie value opens nothing now.
    stale = TestClient(app, raise_server_exceptions=False)
    stale.cookies.set(SESSION_COOKIE, secret)
    resp = stale.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"


def test_sign_out_without_a_session_goes_to_sign_in(anon):
    resp = anon.post("/signout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"


def test_sign_out_after_a_refresh_due_only_clears_the_cookie(app):
    client = TestClient(app, raise_server_exceptions=False)
    with open_store(app.state.store_file) as store:
        store.add_user(User(name="ann", password_hash=None,
                            created="2026-01-01T00:00:00Z"))
        secret = accounts.open_session(store, "ann", "password",
                                       now=time.time() - 120)
    client.cookies.set(SESSION_COOKIE, secret)
    resp = client.post("/signout", data={"session": secret},
                       follow_redirects=False)
    headers = resp.headers.get_list("set-cookie")
    assert len(headers) == 1
    assert cookie_attributes(headers[0])["max-age"] == "0"


# ---------- session expiry and refresh ----------

def test_expired_session_redirects_to_sign_in(app):
    client = TestClient(app, raise_server_exceptions=False)
    with open_store(app.state.store_file) as store:
        store.add_user(User(name="ann", password_hash=None,
                            created="2026-01-01T00:00:00Z"))
        secret = accounts.open_session(
            store, "ann", "password",
            now=time.time() - accounts.SESSION_TTL - 1)
    client.cookies.set(SESSION_COOKIE, secret)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_a_page_that_moves_the_expiry_refreshes_the_cookie(app):
    client = TestClient(app, raise_server_exceptions=False)
    with open_store(app.state.store_file) as store:
        store.add_user(User(name="ann", password_hash=None,
                            created="2026-01-01T00:00:00Z"))
        secret = accounts.open_session(
            store, "ann", "password",
            now=time.time() - accounts.SESSION_WRITE_EVERY - 1)
    client.cookies.set(SESSION_COOKIE, secret)
    resp = client.get("/")
    assert resp.status_code == 200
    header = resp.headers["set-cookie"]
    assert header.startswith(f"{SESSION_COOKIE}={secret};")
    assert cookie_attributes(header)["max-age"] == str(accounts.SESSION_TTL)


def test_a_page_within_the_write_interval_sets_no_cookie(app):
    client = TestClient(app, raise_server_exceptions=False)
    sign_in(client)
    assert "set-cookie" not in client.get("/").headers
