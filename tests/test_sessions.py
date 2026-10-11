"""Tests for server-side browser sessions: open, resume, end, with a controlled clock."""
import hashlib

import pytest

from agent_wiki.server import accounts
from agent_wiki.server.accounts import (
    SESSION_TTL,
    SESSION_WRITE_EVERY,
    end_session,
    hash_secret,
    open_session,
    resume_session,
)
from agent_wiki.server.store import Session, User, open_store, store_path

T0 = 1_800_000_000.0
MINUTE = 60.0
HOUR = 3600.0


@pytest.fixture
def store():
    """A store under the isolated config dir, holding one user."""
    with open_store() as opened:
        opened.add_user(User(name="ann", password_hash=None, created="2026-01-01T00:00:00Z"))
        yield opened


def _rows(store):
    return store.connection.execute("SELECT * FROM sessions").fetchall()


# --- constants and hashing ---------------------------------------------------

def test_constants():
    assert SESSION_TTL == 12 * 3600
    assert SESSION_WRITE_EVERY == 60


def test_hash_secret_is_sha256_hex():
    assert hash_secret("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(hash_secret("x")) == 64


# --- open ----------------------------------------------------------------------

def test_open_returns_secret_and_stores_only_its_hash(store):
    secret = open_session(store, "ann", "password", now=T0)
    assert isinstance(secret, str) and len(secret) >= 40
    session = store.get_session(hash_secret(secret))
    assert session == Session(
        hash=hash_secret(secret), user="ann", method="password",
        expires=T0 + SESSION_TTL, written=T0,
    )
    assert store.get_session(secret) is None


def test_store_never_holds_the_secret(store):
    secret = open_session(store, "ann", "proxy", now=T0)
    resume_session(store, secret, now=T0 + 2 * MINUTE)
    for row in _rows(store):
        assert secret not in [str(value) for value in tuple(row)]
    store.connection.commit()
    raw = store_path().read_bytes()
    wal = store_path().with_name(store_path().name + "-wal")
    if wal.exists():
        raw += wal.read_bytes()
    assert secret.encode() not in raw


def test_open_gives_a_fresh_secret_each_time(store):
    first = open_session(store, "ann", "password", now=T0)
    second = open_session(store, "ann", "password", now=T0)
    assert first != second
    assert len(_rows(store)) == 2


def test_open_sweeps_expired_sessions(store):
    old = open_session(store, "ann", "password", now=T0)
    live = open_session(store, "ann", "password", now=T0 + 6 * HOUR)
    open_session(store, "ann", "password", now=T0 + 12 * HOUR + MINUTE)
    assert store.get_session(hash_secret(old)) is None
    assert store.get_session(hash_secret(live)) is not None
    assert len(_rows(store)) == 2


def test_open_rejects_unknown_method_and_user(store):
    with pytest.raises(ValueError):
        open_session(store, "ann", "carrier-pigeon", now=T0)
    with pytest.raises(ValueError):
        open_session(store, "nobody", "password", now=T0)
    assert _rows(store) == []


def test_open_defaults_now_to_the_clock(store, monkeypatch):
    monkeypatch.setattr(accounts.time, "time", lambda: T0)
    secret = open_session(store, "ann", "password")
    assert store.get_session(hash_secret(secret)).expires == T0 + SESSION_TTL


# --- resume --------------------------------------------------------------------

def test_resume_at_11h59m_keeps_and_extends(store):
    secret = open_session(store, "ann", "password", now=T0)
    later = T0 + 11 * HOUR + 59 * MINUTE
    session, refreshed = resume_session(store, secret, now=later)
    assert refreshed is True
    assert session.user == "ann"
    assert session.method == "password"
    assert session.expires == later + SESSION_TTL
    assert session.written == later
    assert store.get_session(hash_secret(secret)) == session


def test_resume_at_12h1m_expires_and_deletes_the_row(store):
    secret = open_session(store, "ann", "password", now=T0)
    session, refreshed = resume_session(store, secret, now=T0 + 12 * HOUR + MINUTE)
    assert session is None
    assert refreshed is False
    assert store.get_session(hash_secret(secret)) is None
    assert _rows(store) == []


def test_resume_at_exactly_the_expiry_is_expired(store):
    secret = open_session(store, "ann", "password", now=T0)
    assert resume_session(store, secret, now=T0 + SESSION_TTL) == (None, False)
    assert _rows(store) == []


def test_expiry_slides_from_the_last_use(store):
    secret = open_session(store, "ann", "password", now=T0)
    used = T0 + 11 * HOUR
    session, _ = resume_session(store, secret, now=used)
    assert session is not None
    # 12 h 1 m after the open, but only 1 h 1 m after the last use.
    session, _ = resume_session(store, secret, now=T0 + 12 * HOUR + MINUTE)
    assert session is not None
    # 12 h 1 m after the last write: gone.
    assert resume_session(store, secret, now=session.written + 12 * HOUR + MINUTE) == (None, False)
    assert _rows(store) == []


def test_two_uses_ten_seconds_apart_write_once(store):
    secret = open_session(store, "ann", "password", now=T0)
    first_use = T0 + 5 * MINUTE
    session, refreshed = resume_session(store, secret, now=first_use)
    assert refreshed is True
    session2, refreshed2 = resume_session(store, secret, now=first_use + 10)
    assert refreshed2 is False
    assert session2 == session
    assert store.get_session(hash_secret(secret)).written == first_use


def test_use_right_after_open_does_not_write(store):
    secret = open_session(store, "ann", "password", now=T0)
    session, refreshed = resume_session(store, secret, now=T0 + 10)
    assert refreshed is False
    assert session.expires == T0 + SESSION_TTL
    assert session.written == T0


def test_writes_again_once_the_interval_has_passed(store):
    secret = open_session(store, "ann", "password", now=T0)
    _, refreshed = resume_session(store, secret, now=T0 + SESSION_WRITE_EVERY - 1)
    assert refreshed is False
    session, refreshed = resume_session(store, secret, now=T0 + SESSION_WRITE_EVERY)
    assert refreshed is True
    assert session.expires == T0 + SESSION_WRITE_EVERY + SESSION_TTL


def test_resume_unknown_or_empty_secret(store):
    open_session(store, "ann", "password", now=T0)
    assert resume_session(store, "not-a-secret", now=T0 + 1) == (None, False)
    assert resume_session(store, "", now=T0 + 1) == (None, False)
    assert len(_rows(store)) == 1


def test_resume_does_not_match_on_the_stored_hash(store):
    secret = open_session(store, "ann", "password", now=T0)
    # A leaked row's hash is not a cookie.
    assert resume_session(store, hash_secret(secret), now=T0 + 1) == (None, False)


def test_resume_defaults_now_to_the_clock(store, monkeypatch):
    secret = open_session(store, "ann", "password", now=T0)
    monkeypatch.setattr(accounts.time, "time", lambda: T0 + 12 * HOUR + MINUTE)
    assert resume_session(store, secret) == (None, False)


def test_session_goes_with_its_user(store):
    secret = open_session(store, "ann", "password", now=T0)
    store.remove_user("ann")
    assert resume_session(store, secret, now=T0 + 1) == (None, False)


# --- end -----------------------------------------------------------------------

def test_end_session_deletes_the_row(store):
    secret = open_session(store, "ann", "password", now=T0)
    other = open_session(store, "ann", "password", now=T0)
    assert end_session(store, secret) is True
    assert store.get_session(hash_secret(secret)) is None
    assert resume_session(store, secret, now=T0 + 1) == (None, False)
    assert store.get_session(hash_secret(other)) is not None


def test_end_session_unknown_or_empty(store):
    assert end_session(store, "nope") is False
    assert end_session(store, "") is False


def test_sessions_persist_across_store_opens(store):
    secret = open_session(store, "ann", "password", now=T0)
    with open_store() as again:
        session, refreshed = resume_session(again, secret, now=T0 + 2 * MINUTE)
    assert session is not None and refreshed is True
