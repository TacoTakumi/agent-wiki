"""Tests for the SQLite auth store: users, grants, tokens, sessions, failures."""
import sqlite3

import pytest

from agent_wiki.server.store import (
    SESSION_METHODS,
    STORE_FILENAME,
    Conflict,
    Session,
    SqliteStore,
    Token,
    User,
    open_store,
    store_path,
)


def _user(name="ann", password_hash="h1"):
    return User(name=name, password_hash=password_hash, created="2026-01-01T00:00:00Z")


def _token(owner="ann", name="laptop", hash_="tokhash", scope=None, last4="abcd"):
    return Token(owner=owner, name=name, hash=hash_, last4=last4, scope=scope,
                 created="2026-01-02T00:00:00Z")


def _session(hash_="sess", user="ann", method="password", expires=200.0, written=100.0):
    return Session(hash=hash_, user=user, method=method, expires=expires, written=written)


# --- location ---------------------------------------------------------------

def test_store_path_is_under_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path / "cfg"))
    assert store_path() == tmp_path / "cfg" / STORE_FILENAME
    assert STORE_FILENAME == "state.db"


def test_open_store_creates_file_under_config_dir_only(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg" / "nested"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with open_store() as store:
        assert isinstance(store, SqliteStore)
        store.add_user(_user())
    assert (cfg / STORE_FILENAME).is_file()
    assert not (tmp_path / "home").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cfg"]


def test_open_store_explicit_path(tmp_path):
    path = tmp_path / "a" / "b" / "x.db"
    with open_store(path) as store:
        store.add_user(_user())
    assert path.is_file()
    with open_store(path) as store:
        assert store.get_user("ann") == _user()


def test_schema_has_all_tables(tmp_path):
    path = tmp_path / "s.db"
    open_store(path).close()
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"users", "grants", "tokens", "sessions", "signin_failures"} <= names


def test_reopen_is_idempotent(tmp_path):
    path = tmp_path / "s.db"
    with open_store(path) as store:
        store.add_user(_user())
    with open_store(path) as store:
        assert [u.name for u in store.list_users()] == ["ann"]


# --- users ------------------------------------------------------------------

def test_user_round_trip(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user("ann", "h1"))
        store.add_user(_user("bob", None))
        assert store.get_user("ann") == _user("ann", "h1")
        assert store.get_user("bob").password_hash is None
        assert store.get_user("nobody") is None
        assert [u.name for u in store.list_users()] == ["ann", "bob"]


def test_add_user_duplicate_conflicts(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        with pytest.raises(Conflict):
            store.add_user(_user())


def test_set_user_password(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user("ann", "h1"))
        updated = store.set_user_password("ann", "h2")
        assert updated.password_hash == "h2"
        assert store.get_user("ann").password_hash == "h2"
        assert store.set_user_password("ann", None).password_hash is None
        assert store.set_user_password("nobody", "x") is None


# --- grants -----------------------------------------------------------------

def test_grant_round_trip(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        assert store.grants("ann") == {}
        store.set_grant("ann", "main", "reader")
        store.set_grant("ann", "work", "editor")
        assert store.grants("ann") == {"main": "reader", "work": "editor"}


def test_set_grant_replaces_entry_for_key(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.set_grant("ann", "main", "reader")
        store.set_grant("ann", "main", "admin")
        assert store.grants("ann") == {"main": "admin"}


def test_set_grant_requires_user(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        with pytest.raises(ValueError):
            store.set_grant("nobody", "main", "reader")
        assert store.grants("nobody") == {}


def test_store_does_not_validate_role_names(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.set_grant("ann", "main", "anything-goes")
        assert store.grants("ann") == {"main": "anything-goes"}


def test_remove_grant(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.set_grant("ann", "main", "reader")
        store.set_grant("ann", "work", "editor")
        assert store.remove_grant("ann", "main") is True
        assert store.remove_grant("ann", "main") is False
        assert store.grants("ann") == {"work": "editor"}


def test_grants_are_per_user(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user("ann"))
        store.add_user(_user("bob"))
        store.set_grant("ann", "main", "reader")
        store.set_grant("bob", "main", "admin")
        assert store.grants("ann") == {"main": "reader"}
        assert store.grants("bob") == {"main": "admin"}


# --- tokens -----------------------------------------------------------------

def test_token_round_trip_with_and_without_scope(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        plain = _token(name="plain", hash_="h-plain", scope=None)
        scoped = _token(name="scoped", hash_="h-scoped",
                        scope={"main": "reader", "work": "editor"}, last4=None)
        store.add_token(plain)
        store.add_token(scoped)
        assert store.find_token("h-plain") == plain
        assert store.find_token("h-scoped") == scoped
        assert store.find_token("h-scoped").scope == {"main": "reader", "work": "editor"}
        assert store.find_token("missing") is None
        assert [t.name for t in store.list_tokens()] == ["plain", "scoped"]


def test_token_scope_stored_as_json_or_null(tmp_path):
    path = tmp_path / "s.db"
    with open_store(path) as store:
        store.add_user(_user())
        store.add_token(_token(name="a", hash_="ha", scope=None))
        store.add_token(_token(name="b", hash_="hb", scope={"main": "reader"}))
    conn = sqlite3.connect(path)
    rows = dict(conn.execute("SELECT name, scope FROM tokens").fetchall())
    conn.close()
    assert rows["a"] is None
    assert rows["b"] == '{"main": "reader"}'


def test_add_token_duplicate_owner_name_conflicts(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.add_token(_token(hash_="h1"))
        with pytest.raises(Conflict):
            store.add_token(_token(hash_="h2"))


def test_add_token_requires_user(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        with pytest.raises(ValueError):
            store.add_token(_token(owner="nobody"))


def test_list_tokens_by_owner(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user("ann"))
        store.add_user(_user("bob"))
        store.add_token(_token(owner="ann", name="x", hash_="h1"))
        store.add_token(_token(owner="bob", name="y", hash_="h2"))
        assert [t.name for t in store.list_tokens("ann")] == ["x"]
        assert [t.owner for t in store.list_tokens()] == ["ann", "bob"]


def test_revoke_and_touch_token(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.add_token(_token(hash_="h1"))
        assert store.touch_token("h1", "2026-02-02T00:00:00Z") is True
        assert store.find_token("h1").last_used == "2026-02-02T00:00:00Z"
        assert store.touch_token("missing", "x") is False
        assert store.revoke_token("ann", "laptop") is True
        assert store.revoke_token("ann", "laptop") is False
        assert store.find_token("h1") is None


# --- sessions ---------------------------------------------------------------

def test_session_methods():
    assert SESSION_METHODS == ("password", "proxy")


def test_session_round_trip_and_update(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.add_session(_session())
        assert store.get_session("sess") == _session()
        updated = store.update_session("sess", expires=500.0, written=300.0)
        assert updated.expires == 500.0 and updated.written == 300.0
        assert store.update_session("missing", expires=1.0, written=1.0) is None
        assert store.remove_session("sess") is True
        assert store.remove_session("sess") is False
        assert store.get_session("sess") is None


def test_add_session_rejects_bad_method_and_missing_user(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        with pytest.raises(ValueError):
            store.add_session(_session(method="magic"))
        with pytest.raises(ValueError):
            store.add_session(_session(user="nobody"))
        store.add_session(_session())
        with pytest.raises(Conflict):
            store.add_session(_session())


def test_remove_user_sessions_keep_and_expired(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        store.add_session(_session("s1", expires=50.0))
        store.add_session(_session("s2", expires=500.0))
        store.add_session(_session("s3", expires=500.0))
        assert store.remove_user_sessions("ann", keep="s3") == 2
        assert store.get_session("s3") is not None
        assert store.remove_expired_sessions(1000.0) == 1
        assert store.get_session("s3") is None


# --- sign-in failures -------------------------------------------------------

def test_signin_failures(tmp_path):
    with open_store(tmp_path / "s.db") as store:
        store.add_user(_user())
        for at in (30.0, 10.0, 20.0):
            store.add_signin_failure("ann", at)
        assert store.list_signin_failures("ann") == [10.0, 20.0, 30.0]
        assert store.clear_signin_failures("ann", before=25.0) == 2
        assert store.list_signin_failures("ann") == [30.0]
        assert store.clear_signin_failures("ann") == 1
        with pytest.raises(ValueError):
            store.add_signin_failure("nobody", 1.0)


# --- remove_user ------------------------------------------------------------

def _populate(store, name):
    store.add_user(_user(name))
    store.set_grant(name, "main", "reader")
    store.add_token(_token(owner=name, hash_=f"tok-{name}"))
    store.add_session(_session(hash_=f"sess-{name}", user=name))
    store.add_signin_failure(name, 1.0)


def test_remove_user_deletes_everything_it_owns(tmp_path):
    path = tmp_path / "s.db"
    with open_store(path) as store:
        _populate(store, "ann")
        _populate(store, "bob")
        assert store.remove_user("ann") is True
        assert store.remove_user("ann") is False
        assert store.get_user("ann") is None
        assert store.grants("ann") == {}
        assert store.list_tokens("ann") == []
        assert store.get_session("sess-ann") is None
        assert store.list_signin_failures("ann") == []
        # The other user is untouched.
        assert store.grants("bob") == {"main": "reader"}
        assert store.find_token("tok-bob") is not None
        assert store.get_session("sess-bob") is not None
        assert store.list_signin_failures("bob") == [1.0]
    conn = sqlite3.connect(path)
    for table, column in (("grants", "user"), ("tokens", "owner"),
                          ("sessions", "user"), ("signin_failures", "user")):
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = 'ann'").fetchone()[0]
        assert count == 0, table
    conn.close()


def test_remove_user_is_one_transaction(tmp_path):
    """A failure partway through leaves every row in place."""
    path = tmp_path / "s.db"
    with open_store(path) as store:
        _populate(store, "ann")
    # A trigger that aborts the final DELETE of the user row.
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TRIGGER block_user_delete BEFORE DELETE ON users "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    conn.commit()
    conn.close()
    with open_store(path) as store:
        with pytest.raises(sqlite3.DatabaseError):
            store.remove_user("ann")
    with open_store(path) as store:
        assert store.get_user("ann") is not None
        assert store.grants("ann") == {"main": "reader"}
        assert store.find_token("tok-ann") is not None
        assert store.get_session("sess-ann") is not None
        assert store.list_signin_failures("ann") == [1.0]
