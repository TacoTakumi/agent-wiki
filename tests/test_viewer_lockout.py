"""Password sign-in lockout: five wrong passwords for one user within the
window lock that user's password sign-in, even for the right password, until
the lockout ends. The failures live in the store, so a new app on the same
store still sees them; concurrent tries cannot pass the limit; a right
password clears them; and the user's bearer token ignores the lock."""

import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server import accounts
from agent_wiki.server.app import create_app
from agent_wiki.server.store import User, open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from conftest import make_vault, signin_form


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731}
PASSWORD = "correct horse battery"
WRONG = "not the password"
USER = "ann"


def build_app(tmp_vault, tmp_path):
    main = tmp_path / "main"
    if not main.exists():
        make_vault(main)
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=main)})
    return create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)


@pytest.fixture
def clock(monkeypatch):
    """The epoch seconds accounts reads as now; move it with clock[0] += s."""
    now = [time.time()]
    monkeypatch.setattr(accounts, "time",
                        types.SimpleNamespace(time=lambda: now[0]))
    return now


@pytest.fixture
def app(tmp_vault, tmp_path, clock):
    app = build_app(tmp_vault, tmp_path)
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


def post_sign_in(client, name, password):
    data = signin_form(client)
    data.update(name=name, password=password)
    return client.post("/signin", data=data, follow_redirects=False)


def failures(app, name=USER):
    with open_store(app.state.store_file) as store:
        return store.list_signin_failures(name)


def fail(client, times):
    for _ in range(times):
        post_sign_in(client, USER, WRONG)


# ---------- the lockout ----------

def test_four_failures_still_let_the_right_password_in(app, anon):
    for _ in range(accounts.SIGNIN_MAX_FAILURES - 1):
        assert post_sign_in(anon, USER, WRONG).status_code == 400
    assert post_sign_in(anon, USER, PASSWORD).status_code == 303


def test_the_fifth_failure_locks_and_the_sixth_try_gets_429(app, anon):
    for _ in range(accounts.SIGNIN_MAX_FAILURES - 1):
        assert post_sign_in(anon, USER, WRONG).status_code == 400
    assert post_sign_in(anon, USER, WRONG).status_code == 429
    resp = post_sign_in(anon, USER, PASSWORD)
    assert resp.status_code == 429
    assert accounts.SIGNIN_LOCKED in resp.text
    assert '<form method="post" action="/signin">' in resp.text
    assert "set-cookie" not in resp.headers


def test_a_locked_try_records_nothing(app, anon):
    fail(anon, accounts.SIGNIN_MAX_FAILURES)
    post_sign_in(anon, USER, WRONG)
    post_sign_in(anon, USER, PASSWORD)
    assert len(failures(app)) == accounts.SIGNIN_MAX_FAILURES


def test_the_lock_is_per_user_and_name_case_does_not_dodge_it(app, anon):
    with open_store(app.state.store_file) as store:
        store.add_user(User(name="bob",
                            password_hash=accounts.hash_password(
                                "bob password", n=1024),
                            created="2026-01-01T00:00:00Z"))
        store.set_grant("bob", "*", "reader")
    fail(anon, accounts.SIGNIN_MAX_FAILURES)
    assert post_sign_in(anon, USER.upper(), PASSWORD).status_code == 429
    assert post_sign_in(anon, "bob", "bob password").status_code == 303


def test_the_lock_lifts_after_the_lockout(app, anon, clock):
    fail(anon, accounts.SIGNIN_MAX_FAILURES)
    clock[0] += accounts.SIGNIN_LOCKOUT - 1
    assert post_sign_in(anon, USER, PASSWORD).status_code == 429
    clock[0] += 1
    assert post_sign_in(anon, USER, PASSWORD).status_code == 303


def test_failures_spread_past_the_window_do_not_lock(app, anon, clock):
    for _ in range(accounts.SIGNIN_MAX_FAILURES - 1):
        post_sign_in(anon, USER, WRONG)
    clock[0] += accounts.SIGNIN_WINDOW
    assert post_sign_in(anon, USER, WRONG).status_code == 400
    assert post_sign_in(anon, USER, PASSWORD).status_code == 303


def test_a_new_app_on_the_same_store_still_sees_the_lock(
        app, anon, tmp_vault, tmp_path):
    fail(anon, accounts.SIGNIN_MAX_FAILURES)
    fresh = build_app(tmp_vault, tmp_path)
    assert fresh is not app
    assert fresh.state.store_file == app.state.store_file
    client = TestClient(fresh, raise_server_exceptions=False)
    assert post_sign_in(client, USER, PASSWORD).status_code == 429


def test_a_right_password_clears_the_failures(app, anon):
    fail(anon, accounts.SIGNIN_MAX_FAILURES - 1)
    assert post_sign_in(anon, USER, PASSWORD).status_code == 303
    assert failures(app) == []
    fail(anon, accounts.SIGNIN_MAX_FAILURES - 1)
    assert post_sign_in(anon, USER, PASSWORD).status_code == 303


def test_a_new_password_ends_the_lockout(app, anon):
    fail(anon, accounts.SIGNIN_MAX_FAILURES)
    with open_store(app.state.store_file) as store:
        accounts.set_password(store, USER, "a new password")
    assert post_sign_in(anon, USER, "a new password").status_code == 303


def test_an_unknown_name_records_nothing_and_reads_the_same(app, anon):
    for _ in range(accounts.SIGNIN_MAX_FAILURES + 1):
        assert post_sign_in(anon, "nobody", WRONG).status_code == 400
    with open_store(app.state.store_file) as store:
        count = store.connection.execute(
            "SELECT COUNT(*) FROM signin_failures").fetchone()[0]
    assert count == 0


def test_the_locked_users_token_still_opens_the_api(app, anon):
    with open_store(app.state.store_file) as store:
        secret = accounts.mint_token(store, USER, "script", None)
    fail(anon, accounts.SIGNIN_MAX_FAILURES)
    assert post_sign_in(anon, USER, PASSWORD).status_code == 429
    resp = anon.get("/v1/status",
                    headers={"Authorization": f"Bearer {secret}"})
    assert resp.status_code == 200


# ---------- check_password ----------

def test_check_password_answers_user_and_locked(app):
    now = 1_000_000.0
    with open_store(app.state.store_file) as store:
        user, locked = accounts.check_password(store, USER, PASSWORD, now=now)
        assert (user.name, locked) == (USER, False)
        for i in range(accounts.SIGNIN_MAX_FAILURES - 1):
            assert accounts.check_password(
                store, USER, WRONG, now=now + i) == (None, False)
        assert accounts.check_password(
            store, USER, WRONG, now=now + 10) == (None, True)
        assert accounts.check_password(
            store, USER, PASSWORD, now=now + 11) == (None, True)
        user, locked = accounts.check_password(
            store, USER, PASSWORD, now=now + 10 + accounts.SIGNIN_LOCKOUT)
        assert (user.name, locked) == (USER, False)


def test_concurrent_wrong_tries_record_no_more_than_the_limit(app):
    store_file = app.state.store_file
    tries = 20
    start = threading.Barrier(tries)
    answers = []
    answers_guard = threading.Lock()

    def attempt():
        with open_store(store_file) as store:
            start.wait()
            answer = accounts.check_password(store, USER, WRONG)
        with answers_guard:
            answers.append(answer)

    threads = [threading.Thread(target=attempt) for _ in range(tries)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(answers) == tries
    assert len(failures(app)) == accounts.SIGNIN_MAX_FAILURES
    locked = sum(1 for _user, is_locked in answers if is_locked)
    # The failure that locks answers locked, and so does every try after it.
    assert locked == tries - accounts.SIGNIN_MAX_FAILURES + 1
    assert all(user is None for user, _locked in answers)
    with open_store(store_file) as store:
        assert accounts.check_password(store, USER, PASSWORD) == (None, True)
