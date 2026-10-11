"""The user page at /user: the theme choice and the signed-in user's own
tokens. A token is made with a name and scope entries and its secret shows
once, on a response that is never cached; the list shows each token without
its secret; a user revokes only their own tokens. Every form posts the
session field, and a bearer header alone signs nobody in to the page."""

import re

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.session import SESSION_COOKIE
from conftest import VIEWER_USER, make_vault, sign_in, signed_in_client


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}


@pytest.fixture
def app(tmp_vault, tmp_path):
    vaults = {name: VaultEntry(name=name, path=make_vault(tmp_path / name))
              for name in ("main", "notes", "personal")}
    return create_app(tmp_vault, SERVER_CONFIG,
                      viewer=build_vault_catalog(vaults))


@pytest.fixture
def client(app):
    """Signed in as a reader on main and notes only, not on personal."""
    client = signed_in_client(app)
    with open_store(app.state.store_file) as store:
        store.remove_grant(VIEWER_USER, "*")
        store.set_grant(VIEWER_USER, "main", "reader")
        store.set_grant(VIEWER_USER, "notes", "writer")
    return client


def tokens(app, owner=VIEWER_USER):
    with open_store(app.state.store_file) as store:
        return {t.name: t for t in accounts.list_tokens(store, owner)}


def post(client, data, session=None):
    """POST /user with the client's session in the session field, unless
    `session` says otherwise."""
    if session is None:
        session = client.cookies.get(SESSION_COOKIE)
    return client.post("/user", data={"session": session, **data},
                       follow_redirects=False)


def create(client, name, scope=None, mode=None, session=None):
    data = {"op": "add_token", "name": name}
    if mode is not None:
        data["scope_mode"] = mode
    if scope is not None:
        data["scope"] = scope
    return post(client, data, session)


def section(html, section_id):
    found = re.search(rf'<section[^>]*id="{section_id}"[^>]*>(.*?)</section>',
                      html, re.S)
    assert found, section_id
    return found.group(1)


def secrets_on(html):
    return re.findall(r"awp_[A-Za-z0-9_-]{43}", html)


# ---------- the page ----------

def test_page_shows_theme_and_my_tokens(client):
    resp = client.get("/user")
    assert resp.status_code == 200
    assert "<h2>Theme</h2>" in section(resp.text, "theme")
    tokens_html = section(resp.text, "tokens")
    assert "<h2>My tokens</h2>" in tokens_html
    assert "You have no tokens." in tokens_html


def test_header_links_the_user_page_and_has_no_theme_switch(client):
    html = client.get("/user").text
    header = re.search(r'<header class="site-header">(.*?)</header>', html,
                       re.S).group(1)
    assert f'Signed in as <a href="/user">{VIEWER_USER}</a>' in header
    assert 'class="signout"' in header
    assert 'action="/theme"' not in header


def test_scope_rows_offer_star_and_the_readable_vaults_only(client):
    html = client.get("/user").text
    rows = re.findall(r'<option value="([^"=]*)=reader">', html)
    assert rows == ["*", "main", "notes"]
    # Each row's first option leaves the vault out of the token.
    selects = re.findall(r'<select name="scope"[^>]*>\s*<option value="" '
                         r'selected>not in this token</option>', html)
    assert len(selects) == 3
    assert "personal" not in html


def test_every_user_page_form_carries_the_session(client):
    secret = client.cookies.get(SESSION_COOKIE)
    create(client, "laptop")
    html = client.get("/user").text
    forms = re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html,
                       re.S)
    assert len(forms) >= 4  # sign-out, theme, create, one revoke
    for body in forms:
        assert (f'<input type="hidden" name="session" value="{secret}">'
                in body)


# ---------- create ----------

def test_create_shows_the_secret_once_and_is_not_cached(client, app):
    resp = create(client, "laptop")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    shown = secrets_on(resp.text)
    assert len(shown) == 1
    secret = shown[0]
    stored = tokens(app)["laptop"]
    assert stored.hash == accounts.hash_secret(secret)
    assert stored.last4 == secret[-4:]

    after = client.get("/user")
    assert "no-store" not in after.headers.get("cache-control", "")
    assert secrets_on(after.text) == []
    assert secret not in after.text
    row = re.search(r'<tr data-token="laptop">(.*?)</tr>', after.text, re.S)
    assert row, "token not listed"
    assert f"<code>{secret[-4:]}</code>" in row.group(1)
    assert "all my grants" in row.group(1)
    assert "never" in row.group(1)
    assert stored.created in row.group(1)


def test_no_scope_choice_stores_none(client, app):
    resp = create(client, "plain", mode="grants",
                  scope=["main=reader"])  # rows ignored without the choice
    assert resp.status_code == 200
    assert tokens(app)["plain"].scope is None


def test_create_without_a_choice_stores_no_scope(client, app):
    assert create(client, "plain").status_code == 200
    assert tokens(app)["plain"].scope is None


def test_scope_entries_are_stored_as_chosen(client, app):
    resp = create(client, "ci", mode="entries",
                  scope=["*=none", "", "main=reader", "notes=writer", ""])
    assert resp.status_code == 200
    assert tokens(app)["ci"].scope == {"*": "none", "main": "reader",
                                       "notes": "writer"}
    row = re.search(r'<tr data-token="ci">(.*?)</tr>',
                    client.get("/user").text, re.S).group(1)
    assert "*=none, main=reader, notes=writer" in row


@pytest.mark.parametrize("scope", [
    ["main=owner"],
    ["main=reader", "main=writer"],
    ["=reader"],
    ["main"],
])
def test_bad_scope_is_a_400_and_creates_nothing(client, app, scope):
    resp = create(client, "bad", mode="entries", scope=scope)
    assert resp.status_code == 400
    assert 'role="alert"' in resp.text
    assert secrets_on(resp.text) == []
    assert tokens(app) == {}


def test_entries_choice_with_no_row_picked_is_a_400(client, app):
    resp = create(client, "empty", mode="entries", scope=["", "", ""])
    assert resp.status_code == 400
    assert "Pick a role" in resp.text
    assert tokens(app) == {}


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_name_is_a_400(client, app, name):
    resp = create(client, name)
    assert resp.status_code == 400
    assert "The token needs a name." in resp.text
    assert tokens(app) == {}


def test_a_name_in_use_is_a_409_and_keeps_the_first(client, app):
    create(client, "laptop")
    first = tokens(app)["laptop"]
    resp = create(client, "laptop")
    assert resp.status_code == 409
    assert secrets_on(resp.text) == []
    assert tokens(app) == {"laptop": first}


@pytest.mark.parametrize("field", [None, "", "not-the-secret"])
def test_bad_session_field_is_a_403_and_creates_nothing(client, app, field):
    data = {"op": "add_token", "name": "laptop"}
    if field is not None:
        data["session"] = field
    resp = client.post("/user", data=data, follow_redirects=False)
    assert resp.status_code == 403
    assert tokens(app) == {}


def test_another_sessions_secret_is_refused(client, app):
    other = TestClient(app, raise_server_exceptions=False)
    other_secret = sign_in(other, user="other")
    resp = create(client, "laptop", session=other_secret)
    assert resp.status_code == 403
    assert tokens(app) == {}
    assert tokens(app, "other") == {}


def test_unknown_op_is_a_400(client, app):
    resp = post(client, {"op": "grant", "name": "x"})
    assert resp.status_code == 400
    assert "No such action." in resp.text
    assert tokens(app) == {}


# ---------- revoke ----------

def test_revoke_own_token(client, app):
    create(client, "laptop")
    create(client, "ci")
    resp = post(client, {"op": "revoke_token", "name": "laptop"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/user#tokens"
    assert set(tokens(app)) == {"ci"}
    assert 'data-token="laptop"' not in client.get("/user").text


def test_revoke_another_users_token_name_is_a_404(client, app):
    with open_store(app.state.store_file) as store:
        accounts.add_user(store, "other", None)
        accounts.mint_token(store, "other", "shared-name", None)
    resp = post(client, {"op": "revoke_token", "name": "shared-name"})
    assert resp.status_code == 404
    assert set(tokens(app, "other")) == {"shared-name"}


def test_revoke_an_unknown_name_is_a_404_and_keeps_own_tokens(client, app):
    create(client, "laptop")
    resp = post(client, {"op": "revoke_token", "name": "nope"})
    assert resp.status_code == 404
    assert set(tokens(app)) == {"laptop"}


def test_revoke_with_a_bad_session_field_is_a_403(client, app):
    create(client, "laptop")
    resp = post(client, {"op": "revoke_token", "name": "laptop"},
                session="wrong")
    assert resp.status_code == 403
    assert set(tokens(app)) == {"laptop"}


# ---------- a bearer header is no session ----------

@pytest.fixture
def bearer(app, client):
    """A bearer secret of the signed-in user's, and a client with no cookie
    that sends it."""
    secret = secrets_on(create(client, "laptop").text)[0]
    anon = TestClient(app, raise_server_exceptions=False,
                      headers={"Authorization": f"Bearer {secret}"})
    return anon, secret


def test_bearer_only_get_goes_to_sign_in(bearer):
    anon, _ = bearer
    resp = anon.get("/user", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/signin")


@pytest.mark.parametrize("op, name", [("add_token", "new"),
                                      ("revoke_token", "laptop")])
def test_bearer_only_post_goes_to_sign_in_and_changes_nothing(app, bearer,
                                                              op, name):
    anon, secret = bearer
    before = tokens(app)
    resp = anon.post("/user", data={"op": op, "name": name,
                                    "session": secret},
                     follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/signin"
    assert tokens(app) == before
