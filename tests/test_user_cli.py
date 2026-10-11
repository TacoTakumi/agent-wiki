"""Tests for the `awiki user` command group and the account logic behind it."""
import sqlite3

import click
import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.server import accounts
from agent_wiki.server.accounts import (
    AccountError,
    normalise_user_name,
    stale_vault_entries,
    verify_password,
)
from agent_wiki.server.store import Session, Token, open_store, store_path

PASSWORD = "correct horse battery"


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """An isolated config dir whose registry names the vaults 'work' and 'home'."""
    directory = tmp_path / "cfg"
    directory.mkdir()
    (directory / "config.yaml").write_text(yaml.dump({
        "vaults": {
            "work": {"path": str(tmp_path / "work")},
            "home": {"path": str(tmp_path / "home")},
        },
        "default_vault": "work",
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(directory))
    return directory


def run(*args, input=None):
    return CliRunner().invoke(cli, ["user", *args], input=input)


def _twice(password=PASSWORD):
    return f"{password}\n{password}\n"


def _user(name):
    with open_store() as store:
        return store.get_user(name)


def _add_token(owner, name="laptop", hash_="tokhash", scope=None):
    with open_store() as store:
        store.add_token(Token(owner=owner, name=name, hash=hash_, last4="abcd",
                              scope=scope, created="2026-01-02T00:00:00Z"))


# --- names ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ann", "a", "rob.s", "r_b-1", "ann@example.org",
                                  "0day", "a" * 64])
def test_name_rule_accepts(name):
    assert normalise_user_name(name) == name


def test_name_rule_lowercases():
    assert normalise_user_name("Ann") == "ann"


@pytest.mark.parametrize("name", ["", ".ann", "-ann", "an n", "ann/x", "a" * 65,
                                  "ann!", "*"])
def test_name_rule_refuses(name):
    with pytest.raises(AccountError):
        normalise_user_name(name)


# --- add --------------------------------------------------------------------

def test_add_proxy_only_user_appears_in_list(config_dir):
    result = run("add", "ann")
    assert result.exit_code == 0, result.output
    assert "proxy-only" in result.output
    user = _user("ann")
    assert user is not None and user.password_hash is None

    listing = run("list")
    assert listing.exit_code == 0, listing.output
    assert "ann" in listing.output


def test_add_stores_name_lowercase(config_dir):
    assert run("add", "Ann").exit_code == 0
    assert _user("ann") is not None
    assert _user("Ann") is None


def test_add_with_password_prompts_twice_and_stores_a_hash(config_dir):
    result = run("add", "ann", "--password", input=_twice())
    assert result.exit_code == 0, result.output
    user = _user("ann")
    assert user.password_hash is not None
    assert PASSWORD not in user.password_hash
    assert verify_password(PASSWORD, user.password_hash)


def test_add_password_prompt_does_not_echo(config_dir):
    result = run("add", "ann", "--password", input=_twice())
    assert PASSWORD not in result.output


def test_add_mismatched_passwords_exits_non_zero_and_writes_nothing(config_dir):
    result = run("add", "ann", "--password", input=f"{PASSWORD}\nsomething else\n")
    assert result.exit_code != 0
    assert "do not match" in result.output
    assert _user("ann") is None


def test_add_empty_password_is_refused(config_dir):
    result = run("add", "ann", "--password", input="\n\n")
    assert result.exit_code != 0
    assert _user("ann") is None


def test_add_invalid_name_refused_before_any_prompt(config_dir):
    result = run("add", "-bad name", "--password", input=_twice())
    assert result.exit_code != 0
    assert "Password" not in result.output
    with open_store() as store:
        assert store.list_users() == []


def test_add_duplicate_refused_before_any_prompt(config_dir):
    assert run("add", "ann").exit_code == 0
    result = run("add", "ANN", "--password", input=_twice())
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "Password" not in result.output
    assert _user("ann").password_hash is None


def test_password_is_never_an_option_value():
    group = cli.commands["user"]
    for command in group.commands.values():
        for param in command.params:
            if isinstance(param, click.Option):
                assert param.is_flag, f"{command.name} {param.opts} takes a value"


def test_add_refuses_a_password_argument(config_dir):
    result = run("add", "ann", "--password", PASSWORD)
    assert result.exit_code != 0
    assert _user("ann") is None


# --- list -------------------------------------------------------------------

def test_list_with_no_users(config_dir):
    result = run("list")
    assert result.exit_code == 0
    assert "No users" in result.output


def test_list_shows_password_grants_tokens_and_stale_marker(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "*", "admin").exit_code == 0
    assert run("grant", "ann", "gone", "reader").exit_code == 0
    _add_token("ann", "laptop", "h1")
    _add_token("ann", "phone", "h2")

    result = run("list")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    header = next(line for line in lines if line.startswith("ann"))
    assert "no password" in header
    assert "2 tokens" in header
    star = next(line for line in lines if "*" in line and "admin" in line)
    gone = next(line for line in lines if "gone" in line and "reader" in line)
    assert "stale" not in star
    assert "stale" in gone


def test_list_marks_stale_token_scope_entries(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "work", "writer").exit_code == 0
    _add_token("ann", "laptop", "h1", scope={"work": "reader", "gone": "reader", "*": "none"})

    result = run("list")
    assert result.exit_code == 0, result.output
    stale = [line for line in result.output.splitlines() if "stale" in line
             and "names a vault" not in line]
    assert len(stale) == 1
    assert "laptop" in stale[0] and "gone" in stale[0]


def test_list_shows_password_set(config_dir):
    assert run("add", "ann", "--password", input=_twice()).exit_code == 0
    result = run("list")
    header = next(line for line in result.output.splitlines() if line.startswith("ann"))
    assert "password set" in header
    assert "0 tokens" in header


def test_stale_vault_entries_reports_grants_and_scopes(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("add", "bob").exit_code == 0
    assert run("grant", "ann", "*", "admin").exit_code == 0
    assert run("grant", "ann", "gone", "reader").exit_code == 0
    assert run("grant", "bob", "work", "reader").exit_code == 0
    _add_token("bob", "ci", "h1", scope={"old": "reader", "*": "reader", "work": "reader"})
    _add_token("bob", "free", "h2", scope=None)
    with open_store() as store:
        entries = stale_vault_entries(store, {"work", "home"})
    assert entries == [("ann", "grant", "gone"), ("bob", "token ci", "old")]


# --- passwd -----------------------------------------------------------------

def _seed_sessions_and_failures(name):
    with open_store() as store:
        store.add_session(Session(hash="s1", user=name, method="password",
                                  expires=2e9, written=1e9))
        store.add_session(Session(hash="s2", user=name, method="proxy",
                                  expires=2e9, written=1e9))
        store.add_signin_failure(name, 1e9)
        store.add_signin_failure(name, 1e9 + 1)


def test_passwd_sets_password_ends_sessions_and_clears_failures(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("add", "bob").exit_code == 0
    _seed_sessions_and_failures("ann")
    with open_store() as store:
        store.add_session(Session(hash="b1", user="bob", method="proxy",
                                  expires=2e9, written=1e9))

    result = run("passwd", "ann", input=_twice())
    assert result.exit_code == 0, result.output
    with open_store() as store:
        user = store.get_user("ann")
        assert verify_password(PASSWORD, user.password_hash)
        assert store.get_session("s1") is None
        assert store.get_session("s2") is None
        assert store.list_signin_failures("ann") == []
        assert store.get_session("b1") is not None


def test_passwd_clear_makes_user_proxy_only(config_dir):
    assert run("add", "ann", "--password", input=_twice()).exit_code == 0
    _seed_sessions_and_failures("ann")

    result = run("passwd", "ann", "--clear")
    assert result.exit_code == 0, result.output
    assert "Password" not in result.output
    with open_store() as store:
        assert store.get_user("ann").password_hash is None
        assert store.get_session("s1") is None
        assert store.list_signin_failures("ann") == []


def test_passwd_mismatch_keeps_password_and_sessions(config_dir):
    assert run("add", "ann", "--password", input=_twice()).exit_code == 0
    _seed_sessions_and_failures("ann")
    before = _user("ann").password_hash

    result = run("passwd", "ann", input="one\ntwo\n")
    assert result.exit_code != 0
    with open_store() as store:
        assert store.get_user("ann").password_hash == before
        assert store.get_session("s1") is not None


def test_passwd_unknown_user_refused_before_prompt(config_dir):
    result = run("passwd", "nobody", input=_twice())
    assert result.exit_code != 0
    assert "No user" in result.output
    assert "Password" not in result.output


def test_set_password_refuses_empty(config_dir):
    assert run("add", "ann").exit_code == 0
    with open_store() as store, pytest.raises(AccountError):
        accounts.set_password(store, "ann", "")


# --- remove -----------------------------------------------------------------

def test_remove_empties_every_table_for_the_name(config_dir):
    assert run("add", "ann", "--password", input=_twice()).exit_code == 0
    assert run("add", "bob").exit_code == 0
    assert run("grant", "ann", "*", "admin").exit_code == 0
    assert run("grant", "bob", "work", "reader").exit_code == 0
    _add_token("ann", "laptop", "h1", scope={"work": "reader"})
    _seed_sessions_and_failures("ann")

    result = run("remove", "ann")
    assert result.exit_code == 0, result.output

    connection = sqlite3.connect(store_path())
    try:
        for table, column in [("users", "name"), ("grants", "user"), ("tokens", "owner"),
                              ("sessions", "user"), ("signin_failures", "user")]:
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", ("ann",)
            ).fetchone()[0]
            assert count == 0, table
    finally:
        connection.close()
    with open_store() as store:
        assert store.get_user("bob") is not None
        assert store.grants("bob") == {"work": "reader"}


def test_remove_unknown_user_exits_non_zero(config_dir):
    result = run("remove", "nobody")
    assert result.exit_code != 0
    assert "No user" in result.output


# --- grant / ungrant --------------------------------------------------------

def _grants(name):
    with open_store() as store:
        return store.grants(name)


def test_grant_then_list_shows_entry(config_dir):
    assert run("add", "ann").exit_code == 0
    result = run("grant", "ann", "work", "writer")
    assert result.exit_code == 0, result.output
    assert _grants("ann") == {"work": "writer"}
    listing = run("list").output
    assert any("work" in line and "writer" in line for line in listing.splitlines())


def test_second_grant_replaces_entry(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "work", "writer").exit_code == 0
    assert run("grant", "ann", "work", "none").exit_code == 0
    assert _grants("ann") == {"work": "none"}


def test_grant_wildcard(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "*", "reader").exit_code == 0
    assert _grants("ann") == {"*": "reader"}


def test_ungrant_removes_entry(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "work", "writer").exit_code == 0
    assert run("grant", "ann", "*", "reader").exit_code == 0
    result = run("ungrant", "ann", "work")
    assert result.exit_code == 0, result.output
    assert _grants("ann") == {"*": "reader"}


def test_ungrant_missing_entry_exits_non_zero(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "*", "reader").exit_code == 0
    result = run("ungrant", "ann", "work")
    assert result.exit_code != 0
    assert _grants("ann") == {"*": "reader"}


def test_grant_unknown_role_refused_map_unchanged(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "work", "reader").exit_code == 0
    result = run("grant", "ann", "work", "owner")
    assert result.exit_code != 0
    assert "owner" in result.output
    assert _grants("ann") == {"work": "reader"}


def test_grant_unknown_user_refused(config_dir):
    result = run("grant", "nobody", "work", "reader")
    assert result.exit_code != 0
    assert "No user" in result.output
    with open_store() as store:
        assert store.grants("nobody") == {}


def test_ungrant_unknown_user_refused(config_dir):
    result = run("ungrant", "nobody", "work")
    assert result.exit_code != 0
    assert "No user" in result.output


def test_grant_name_is_case_insensitive(config_dir):
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ANN", "work", "reader").exit_code == 0
    assert _grants("ann") == {"work": "reader"}


def test_list_does_not_mark_local_stale_under_a_bare_path_override(
        config_dir, tmp_path, monkeypatch):
    from conftest import make_vault
    monkeypatch.setenv("AWIKI_VAULT", str(make_vault(tmp_path / "bare")))
    assert run("add", "ann").exit_code == 0
    assert run("grant", "ann", "local", "reader").exit_code == 0
    result = run("list")
    assert result.exit_code == 0, result.output
    local = next(line for line in result.output.splitlines()
                 if "local" in line and "reader" in line)
    assert "stale" not in local
