"""The viewer shows a signed-in user only the vaults their grants let them
read: the vault list, search and its vault choices, and every vault view.
A vault they may not read answers exactly as a vault that does not exist."""

import pytest

from agent_wiki.registry import VaultEntry
from agent_wiki.server.access import readable_vaults
from agent_wiki.server.app import create_app
from agent_wiki.server.store import open_store
from agent_wiki.viewer.catalog import build_vault_catalog, narrow_catalog
from conftest import VIEWER_USER, make_vault, signed_in_client


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731, "tokens": []}


def page(title, tags, body):
    return (f"---\ntitle: {title}\ntopic: research\ntags: [{', '.join(tags)}]\n"
            f"created: 2026-01-01\nupdated: 2026-01-02\n---\n\n"
            f"# {title}\n\n{body}\n")


@pytest.fixture
def vaults(tmp_path):
    """Two vaults, main and personal, each with a page that carries a shared
    tag; personal's page also carries a tag and a word no main page has."""
    main = make_vault(tmp_path / "main")
    personal = make_vault(tmp_path / "personal")
    (main / "research" / "open-notes.md").write_text(
        page("Open Notes", ["shared"], "Common ground for everyone."))
    (personal / "research" / "diary.md").write_text(
        page("Secret Diary", ["shared", "private-only"],
             "Common ground and zanzibarquux."))
    return {"main": main, "personal": personal}


@pytest.fixture
def app(tmp_vault, vaults):
    registry = {name: VaultEntry(name=name, path=path)
                for name, path in vaults.items()}
    return create_app(tmp_vault, SERVER_CONFIG,
                      viewer=build_vault_catalog(registry))


@pytest.fixture
def client(app):
    """Signed in as a reader on every vault but personal."""
    client = signed_in_client(app, role="reader")
    with open_store(app.state.store_file) as store:
        store.set_grant(VIEWER_USER, "personal", "none")
    return client


def assert_hides_personal(html):
    assert "personal" not in html
    assert "Secret Diary" not in html


# --- the shared decision ---------------------------------------------------


def test_readable_vaults_keeps_reader_and_up_in_order():
    grants = {"*": "reader", "personal": "none", "ops": "admin",
              "team": "writer"}
    names = ["team", "main", "personal", "ops"]
    assert readable_vaults(grants, names) == ["team", "main", "ops"]


def test_readable_vaults_without_a_wildcard_keeps_only_named_grants():
    assert readable_vaults({"main": "reader"}, ["main", "work"]) == ["main"]


def test_readable_vaults_with_no_grants_keeps_nothing():
    assert readable_vaults({}, ["main", "work"]) == []
    assert readable_vaults({"*": "none"}, ["main"]) == []


def test_narrow_catalog_drops_served_and_unavailable_names(tmp_path):
    registry = {
        "main": VaultEntry(name="main", path=make_vault(tmp_path / "main")),
        "work": VaultEntry(name="work", path=make_vault(tmp_path / "work")),
        "gone": VaultEntry(name="gone", path=tmp_path / "gone"),
        "lost": VaultEntry(name="lost", path=tmp_path / "lost"),
        "team": VaultEntry(name="team", url="https://wiki.example.com",
                           token="tok-1"),
    }
    narrowed = narrow_catalog(build_vault_catalog(registry), ["main", "gone"])
    assert list(narrowed.vaults) == ["main"]
    assert narrowed.unavailable == ("gone",)
    assert narrowed.skipped_remote == 1


# --- the viewer's pages ----------------------------------------------------


def test_vault_list_hides_an_unreadable_vault(client):
    html = client.get("/").text
    assert "/vaults/main/" in html
    assert_hides_personal(html)


def test_search_results_hide_an_unreadable_vault(client):
    resp = client.get("/search", params={"q": "zanzibarquux"})
    assert resp.status_code == 200
    assert_hides_personal(resp.text)
    assert "zanzibarquux" in resp.text  # the query echo, not a hit


def test_search_for_a_shared_word_shows_only_readable_hits(client):
    html = client.get("/search", params={"q": "common ground"}).text
    assert "Open Notes" in html
    assert_hides_personal(html)


def test_search_naming_an_unreadable_vault_searches_readable_ones(client):
    html = client.get("/search", params={"q": "zanzibarquux",
                                         "vault": "personal"}).text
    # The theme switch echoes the request's own URL back, so the name the
    # user typed is on the page; no checkbox, hit or link names it.
    assert 'value="personal"' not in html
    assert "/vaults/personal/" not in html
    assert "Secret Diary" not in html
    assert 'value="main" checked' in html


def test_search_page_offers_only_readable_vaults(client):
    html = client.get("/search").text
    assert 'value="main"' in html
    assert_hides_personal(html)


def test_tag_page_for_an_unreadable_vaults_own_tag_is_not_found(client):
    readable = client.get("/vaults/main/-/tags/private-only")
    assert readable.status_code == 200
    assert_hides_personal(readable.text)
    hidden = client.get("/vaults/personal/-/tags/private-only")
    assert hidden.status_code == 404
    assert_hides_personal(hidden.text)


def test_shared_tag_page_lists_only_readable_pages(client):
    html = client.get("/vaults/main/-/tags/shared").text
    assert "Open Notes" in html
    assert_hides_personal(html)


@pytest.mark.parametrize("suffix", ["", "research/", "research/diary",
                                    "-/tags/shared"])
def test_unreadable_vault_answers_as_a_missing_one(client, suffix):
    hidden = client.get(f"/vaults/personal/{suffix}")
    missing = client.get(f"/vaults/nonexistent/{suffix}")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.content == missing.content
    assert_hides_personal(hidden.text)


def test_readable_vault_still_answers(client):
    assert client.get("/vaults/main/").status_code == 200
    assert client.get("/vaults/main/research/open-notes").status_code == 200


# --- grant changes apply at the next request -------------------------------


def test_grant_change_empties_the_vault_list_at_the_next_request(app, client):
    assert "/vaults/main/" in client.get("/").text
    with open_store(app.state.store_file) as store:
        store.remove_grant(VIEWER_USER, "personal")
        store.set_grant(VIEWER_USER, "*", "none")
        assert store.grants(VIEWER_USER) == {"*": "none"}
    html = client.get("/").text
    assert "/vaults/main/" not in html
    assert "/vaults/personal/" not in html
    assert "No local vaults to serve." in html
    assert client.get("/vaults/main/").status_code == 404


def test_an_unknown_stored_role_hides_the_vault_and_lists_the_rest(app, client):
    with open_store(app.state.store_file) as store:
        store.set_grant(VIEWER_USER, "personal", "owner")
    response = client.get("/")
    assert response.status_code == 200
    assert "/vaults/main/" in response.text
    assert_hides_personal(response.text)
    assert client.get("/vaults/personal/").status_code == 404


def test_grant_given_shows_the_vault_at_the_next_request(app, client):
    assert_hides_personal(client.get("/").text)
    with open_store(app.state.store_file) as store:
        store.set_grant(VIEWER_USER, "personal", "reader")
    assert "/vaults/personal/" in client.get("/").text
    assert client.get("/vaults/personal/").status_code == 200
