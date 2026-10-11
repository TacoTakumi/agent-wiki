"""Reverse-proxy sign-in: with auth_proxy on, a trusted socket peer that sends
the configured header once, naming a known user, opens a proxy session for
that user. The header from any other peer, a duplicated header and a header
on the /v1 API sign no one in. An unknown name gets a 403 and no account. The
session cookie goes out Secure over https, or when a trusted peer says
X-Forwarded-Proto: https."""

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import User, open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.session import PROXY_METHOD, SESSION_COOKIE
from conftest import make_vault, signin_form


HEADER = "X-WEBAUTH-USER"
TRUSTED = ("127.0.0.1", 50000)
UNTRUSTED = ("203.0.113.9", 50000)
PROXY_CONFIG = {"bind": "127.0.0.1", "port": 8731, "auth_proxy": True}


def build_app(tmp_vault, tmp_path, config):
    main = make_vault(tmp_path / "main")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=main)})
    app = create_app(tmp_vault, config, viewer=catalog)
    for name in ("alice", "bob"):
        with open_store(app.state.store_file) as store:
            store.add_user(User(name=name, password_hash=None,
                                created="2026-01-01T00:00:00Z"))
            store.set_grant(name, "*", "reader")
    return app


@pytest.fixture
def app(tmp_vault, tmp_path):
    return build_app(tmp_vault, tmp_path, PROXY_CONFIG)


def client_from(app, peer, **kwargs):
    return TestClient(app, client=peer, raise_server_exceptions=False,
                      follow_redirects=False, **kwargs)


def sessions(app):
    """Every stored session as (user, method)."""
    with open_store(app.state.store_file) as store:
        return sorted(tuple(row) for row in store.connection.execute(
            "SELECT user, method FROM sessions"))


def user_names(app):
    with open_store(app.state.store_file) as store:
        return sorted(user.name for user in store.list_users())


def session_cookie(response):
    """The Set-Cookie header that sets the session cookie, or None."""
    for value in response.headers.get_list("set-cookie"):
        if value.startswith(f"{SESSION_COOKIE}="):
            return value
    return None


def test_trusted_peer_with_header_opens_proxy_session(app):
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={HEADER: "Alice"})
    assert response.status_code == 200
    assert "Signed in as" in response.text and "alice" in response.text
    assert sessions(app) == [("alice", PROXY_METHOD)]
    cookie = session_cookie(response)
    assert cookie is not None
    assert "httponly" in cookie.lower()
    # The cookie the response set resumes the same session, header or not.
    assert client.get("/").status_code == 200
    assert sessions(app) == [("alice", PROXY_METHOD)]


def test_untrusted_peer_header_is_ignored(app):
    client = client_from(app, UNTRUSTED)
    response = client.get("/", headers={HEADER: "alice"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")
    assert sessions(app) == []


def test_forwarded_for_does_not_make_a_peer_trusted(app):
    client = client_from(app, UNTRUSTED)
    response = client.get("/", headers={
        HEADER: "alice", "X-Forwarded-For": "127.0.0.1",
        "Forwarded": "for=127.0.0.1"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")
    assert sessions(app) == []


def test_header_is_ignored_while_proxy_sign_in_is_off(tmp_vault, tmp_path):
    app = build_app(tmp_vault, tmp_path, {"bind": "127.0.0.1", "port": 8731})
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={HEADER: "alice"})
    assert response.status_code == 303
    assert sessions(app) == []


def test_unknown_user_gets_403_and_no_account(app):
    before = user_names(app)
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={HEADER: "mallory"})
    assert response.status_code == 403
    assert "mallory" in response.text
    assert "<form method=\"post\" action=\"/signin\"" not in response.text
    assert session_cookie(response) is None
    assert user_names(app) == before
    assert sessions(app) == []


def test_unknown_user_leaves_a_live_session_alone(app):
    client = client_from(app, TRUSTED)
    with open_store(app.state.store_file) as store:
        secret = accounts.open_session(store, "alice", "password")
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/", headers={HEADER: "mallory"})
    assert response.status_code == 403
    assert sessions(app) == [("alice", "password")]


def test_unknown_user_name_is_escaped(app):
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={HEADER: "<b>x</b>"})
    assert response.status_code == 403
    assert "<b>x</b>" not in response.text


def test_header_for_another_user_replaces_the_session(app):
    client = client_from(app, TRUSTED)
    with open_store(app.state.store_file) as store:
        secret = accounts.open_session(store, "alice", "password")
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/", headers={HEADER: "bob"})
    assert response.status_code == 200
    assert "bob" in response.text
    assert sessions(app) == [("bob", PROXY_METHOD)]
    cookie = session_cookie(response)
    assert cookie is not None and secret not in cookie
    # The old cookie no longer opens anything.
    stale = client_from(app, UNTRUSTED)
    stale.cookies.set(SESSION_COOKIE, secret)
    assert stale.get("/").status_code == 303


def test_header_for_the_live_user_keeps_the_session(app):
    client = client_from(app, TRUSTED)
    with open_store(app.state.store_file) as store:
        secret = accounts.open_session(store, "alice", "password")
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/", headers={HEADER: "alice"})
    assert response.status_code == 200
    assert sessions(app) == [("alice", "password")]


def test_duplicated_header_is_not_trusted(app):
    client = client_from(app, TRUSTED)
    response = client.get("/", headers=[(HEADER, "alice"), (HEADER, "alice")])
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")
    assert sessions(app) == []


def test_underscore_spelling_is_another_header(app):
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={"X_WEBAUTH_USER": "alice"})
    assert response.status_code == 303
    assert sessions(app) == []


def test_configured_header_name_is_used(tmp_vault, tmp_path):
    app = build_app(tmp_vault, tmp_path,
                    dict(PROXY_CONFIG, auth_proxy_header="X-Remote-User"))
    client = client_from(app, TRUSTED)
    assert client.get("/", headers={HEADER: "alice"}).status_code == 303
    assert client.get("/", headers={"X-Remote-User": "alice"}).status_code == 200
    assert sessions(app) == [("alice", PROXY_METHOD)]


def test_sign_in_page_sends_a_proxied_user_on(app):
    client = client_from(app, TRUSTED)
    response = client.get("/signin?next=/search", headers={HEADER: "alice"})
    assert response.status_code == 303
    assert response.headers["location"] == "/search"
    assert sessions(app) == [("alice", PROXY_METHOD)]


def test_header_never_authenticates_the_api(app):
    client = client_from(app, TRUSTED)
    response = client.get("/v1/status", headers={HEADER: "alice"})
    assert response.status_code == 401
    assert sessions(app) == []


def test_session_cookie_never_authenticates_the_api(app):
    client = client_from(app, TRUSTED)
    client.get("/", headers={HEADER: "alice"})
    assert client.cookies.get(SESSION_COOKIE)
    assert client.get("/v1/status").status_code == 401


def test_secure_from_trusted_peer_forwarding_https(app):
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={HEADER: "alice",
                                        "X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert "secure" in session_cookie(response).lower()


def test_not_secure_over_plain_http_from_trusted_peer(app):
    client = client_from(app, TRUSTED)
    response = client.get("/", headers={HEADER: "alice"})
    assert "secure" not in session_cookie(response).lower()


def test_forwarded_proto_from_untrusted_peer_is_ignored(app):
    with open_store(app.state.store_file) as store:
        user_hash = accounts.hash_password("correct horse battery")
        store.connection.execute(
            "UPDATE users SET password_hash = ? WHERE name = 'alice'",
            (user_hash,))
        store.connection.commit()
    client = client_from(app, UNTRUSTED)
    data = signin_form(client)
    data.update(name="alice", password="correct horse battery")
    response = client.post("/signin", data=data,
                           headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 303
    assert "secure" not in session_cookie(response).lower()


def test_forwarded_proto_from_trusted_peer_counts_with_proxy_off(
        tmp_vault, tmp_path):
    app = build_app(tmp_vault, tmp_path, {"bind": "127.0.0.1", "port": 8731})
    with open_store(app.state.store_file) as store:
        secret = accounts.open_session(store, "alice", "password", now=0)
    # A session last written long ago is refreshed, so the response sets
    # the cookie again.
    with open_store(app.state.store_file) as store:
        store.update_session(accounts.hash_secret(secret),
                             expires=10 ** 12, written=0)
    client = client_from(app, TRUSTED)
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert "secure" in session_cookie(response).lower()


def test_https_request_sets_secure(app):
    client = client_from(app, UNTRUSTED, base_url="https://testserver")
    with open_store(app.state.store_file) as store:
        secret = accounts.open_session(store, "alice", "password", now=0)
        store.update_session(accounts.hash_secret(secret),
                             expires=10 ** 12, written=0)
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/")
    assert response.status_code == 200
    assert "secure" in session_cookie(response).lower()


@pytest.mark.parametrize("peer, secure", [(("10.0.0.5", 50000), True),
                                          (("10.0.0.6", 50000), False)])
def test_a_remote_tls_proxy_in_the_list_sets_secure_with_proxy_off(
        tmp_vault, tmp_path, peer, secure):
    app = build_app(tmp_vault, tmp_path,
                    {"bind": "127.0.0.1", "port": 8731, "auth_proxy": False,
                     "auth_trusted_proxies": ["10.0.0.5"]})
    with open_store(app.state.store_file) as store:
        user_hash = accounts.hash_password("correct horse battery", n=1024)
        store.connection.execute(
            "UPDATE users SET password_hash = ? WHERE name = 'alice'",
            (user_hash,))
        store.connection.commit()
    client = client_from(app, peer)
    data = signin_form(client)
    data.update(name="alice", password="correct horse battery")
    response = client.post("/signin", data=data,
                           headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 303
    assert ("secure" in session_cookie(response).lower()) is secure
