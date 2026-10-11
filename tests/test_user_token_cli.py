"""Tests for `awiki user token` and the token logic behind it."""
import re

import pytest
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.server import accounts
from agent_wiki.server.accounts import AccountError, hash_secret
from agent_wiki.server.store import open_store, store_path

SECRET_PATTERN = re.compile(r"awp_[A-Za-z0-9_-]{43}")


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    directory = tmp_path / "cfg"
    directory.mkdir()
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(directory))
    return directory


@pytest.fixture
def ann(config_dir):
    with open_store() as store:
        accounts.add_user(store, "ann", None)
    return "ann"


def run(*args):
    return CliRunner().invoke(cli, ["user", "token", *args])


def _secrets_in(output):
    return SECRET_PATTERN.findall(output)


def _add(*args):
    result = run("add", *args)
    assert result.exit_code == 0, result.output
    found = _secrets_in(result.output)
    assert len(found) == 1, result.output
    return result, found[0]


# --- add --------------------------------------------------------------------

def test_add_prints_secret_once_and_stores_only_hash(ann):
    result, secret = _add("ann", "laptop")
    assert re.fullmatch(r"awp_[A-Za-z0-9_-]{43}", secret)
    assert result.output.count(secret) == 1
    assert "store it now, it will not be shown again" in result.output.lower()
    with open_store() as store:
        [token] = store.list_tokens("ann")
    assert token.name == "laptop"
    assert token.hash == hash_secret(secret)
    assert token.last4 == secret[-4:]
    assert token.scope is None
    assert token.created
    assert token.last_used is None


def test_store_file_holds_no_copy_of_the_secret(ann):
    _result, secret = _add("ann", "laptop")
    raw = b"".join(
        path.read_bytes()
        for path in store_path().parent.iterdir()
        if path.name.startswith(store_path().name)
    )
    assert secret.encode() not in raw
    assert secret[len(accounts.TOKEN_PREFIX):].encode() not in raw
    assert hash_secret(secret).encode() in raw


def test_add_with_scope_stores_the_scope(ann):
    result, _secret = _add("ann", "ci", "--scope", "work=reader", "--scope", "*=none")
    assert "work=reader" in result.output
    with open_store() as store:
        [token] = store.list_tokens("ann")
    assert token.scope == {"work": "reader", "*": "none"}


def test_add_accepts_owner_in_any_case(ann):
    _add("ANN", "laptop")
    with open_store() as store:
        assert [t.owner for t in store.list_tokens()] == ["ann"]


def test_duplicate_name_for_same_user_refused(ann):
    _add("ann", "laptop")
    result = run("add", "ann", "laptop")
    assert result.exit_code != 0
    assert "already has a token named 'laptop'" in result.output
    assert not _secrets_in(result.output)
    with open_store() as store:
        assert len(store.list_tokens("ann")) == 1


def test_same_name_for_another_user_allowed(ann):
    with open_store() as store:
        accounts.add_user(store, "bob", None)
    _add("ann", "laptop")
    _add("bob", "laptop")


@pytest.mark.parametrize("scope", ["work=owner", "work", "=reader", "work=", "work=reader,x"])
def test_bad_scope_refused(ann, scope):
    result = run("add", "ann", "ci", "--scope", scope)
    assert result.exit_code != 0
    assert "scope" in result.output.lower()
    assert not _secrets_in(result.output)
    with open_store() as store:
        assert store.list_tokens("ann") == []


def test_role_owner_refused_names_the_roles(ann):
    result = run("add", "ann", "ci", "--scope", "work=owner")
    assert result.exit_code != 0
    assert "'owner'" in result.output
    assert "none, reader, writer, admin" in result.output


def test_add_for_unknown_user_refused(config_dir):
    result = run("add", "nobody", "laptop")
    assert result.exit_code != 0
    assert "No user named 'nobody'" in result.output


def test_add_with_empty_name_refused(ann):
    result = run("add", "ann", "")
    assert result.exit_code != 0
    assert not _secrets_in(result.output)


# --- list -------------------------------------------------------------------

def test_list_shows_details_but_never_the_secret(ann):
    _r, plain = _add("ann", "laptop")
    _r, scoped = _add("ann", "ci", "--scope", "work=writer")
    result = run("list", "ann")
    assert result.exit_code == 0, result.output
    out = result.output
    assert plain not in out and scoped not in out
    assert not _secrets_in(out)
    assert hash_secret(plain) not in out
    assert "laptop" in out and "ci" in out
    assert plain[-4:] in out and scoped[-4:] in out
    assert "work=writer" in out
    assert "all of the owner's grants" in out
    assert "created:" in out
    assert "last used: never" in out


def test_list_shows_last_use(ann):
    _r, secret = _add("ann", "laptop")
    with open_store() as store:
        accounts.token_for(store, secret, now=1_700_000_000)
    out = run("list", "ann").output
    assert "last used: 2023-11-14T22:13:20Z" in out


def test_list_with_no_tokens(ann):
    result = run("list", "ann")
    assert result.exit_code == 0
    assert "no tokens" in result.output


def test_list_unknown_user_fails(config_dir):
    assert run("list", "nobody").exit_code != 0


# --- revoke -----------------------------------------------------------------

def test_revoke_deletes_the_token(ann):
    _r, secret = _add("ann", "laptop")
    result = run("revoke", "ann", "laptop")
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output
    with open_store() as store:
        assert store.list_tokens("ann") == []
        assert accounts.token_for(store, secret) is None


def test_revoke_unknown_token_fails(ann):
    result = run("revoke", "ann", "laptop")
    assert result.exit_code != 0
    assert "no token named 'laptop'" in result.output


def test_revoke_unknown_user_fails(config_dir):
    assert run("revoke", "nobody", "laptop").exit_code != 0


# --- the old top-level group --------------------------------------------------

@pytest.mark.parametrize("args", [["token", "list"], ["token", "add", "x", "--role", "admin"]])
def test_top_level_token_group_is_gone(config_dir, args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "No such command 'token'" in result.output


# --- accounts: parse_scope, mint_token, token_for -----------------------------

def test_parse_scope():
    assert accounts.parse_scope([]) is None
    assert accounts.parse_scope(None) is None
    assert accounts.parse_scope(["work=reader", "*=none"]) == {"work": "reader", "*": "none"}
    for bad in (["work"], ["=reader"], ["work=owner"], ["work=reader", "work=writer"]):
        with pytest.raises(AccountError):
            accounts.parse_scope(bad)


def test_mint_token_refuses_bad_input(ann):
    with open_store() as store:
        with pytest.raises(AccountError):
            accounts.mint_token(store, "ann", "x", {"work": "owner"})
        with pytest.raises(AccountError):
            accounts.mint_token(store, "ann", "x", {"": "reader"})
        with pytest.raises(AccountError):
            accounts.mint_token(store, "ann", "", None)
        with pytest.raises(AccountError):
            accounts.mint_token(store, "nobody", "x", None)
        assert store.list_tokens() == []


def test_mint_token_uses_injected_time(ann):
    with open_store() as store:
        accounts.mint_token(store, "ann", "x", None, now="2026-01-02T03:04:05Z")
        [token] = store.list_tokens("ann")
    assert token.created == "2026-01-02T03:04:05Z"


def test_token_for_unknown_or_empty_secret(ann):
    with open_store() as store:
        assert accounts.token_for(store, "") is None
        assert accounts.token_for(store, "awp_" + "x" * 43) is None


def test_token_for_writes_last_use_once_a_minute(ann):
    start = 1_700_000_000
    with open_store() as store:
        secret = accounts.mint_token(store, "ann", "laptop", {"work": "reader"})
        first = accounts.token_for(store, secret, now=start)
        assert first.owner == "ann" and first.name == "laptop"
        assert first.scope == {"work": "reader"}
        assert first.last_used == "2023-11-14T22:13:20Z"
        assert store.find_token(hash_secret(secret)).last_used == first.last_used

        # Within the minute: found, but nothing written.
        again = accounts.token_for(
            store, secret, now=start + accounts.TOKEN_WRITE_EVERY - 1
        )
        assert again.last_used == first.last_used
        assert store.find_token(hash_secret(secret)).last_used == first.last_used

        # A minute on: written.
        later = accounts.token_for(store, secret, now=start + accounts.TOKEN_WRITE_EVERY)
        assert later.last_used == "2023-11-14T22:14:20Z"
        assert store.find_token(hash_secret(secret)).last_used == later.last_used


def test_list_and_revoke_tokens(ann):
    with open_store() as store:
        accounts.mint_token(store, "ann", "b", None)
        accounts.mint_token(store, "ann", "a", None)
        assert [t.name for t in accounts.list_tokens(store, "ANN")] == ["a", "b"]
        assert accounts.revoke_token(store, "ann", "a") is True
        with pytest.raises(AccountError):
            accounts.revoke_token(store, "ann", "a")
        assert [t.name for t in accounts.list_tokens(store, "ann")] == ["b"]
