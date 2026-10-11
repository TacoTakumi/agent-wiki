"""Tests for moving server.yaml tokens into the user store, and for serve
refusing to start while server.yaml still holds tokens."""
import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.server import accounts
from agent_wiki.server.accounts import AccountError, hash_secret
from agent_wiki.server.store import Token, open_store
from agent_wiki.server_config import hash_token, server_config_path

MIGRATE = "awiki user token migrate --to <user>"


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


def _entries():
    return [
        {"name": "laptop", "role": "reader", "hash": hash_token("reader-secret")},
        {"name": "ops", "role": "admin", "hash": hash_token("admin-secret")},
    ]


def _write_server_yaml(**data):
    path = server_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))
    return path


def _read_server_yaml():
    return yaml.safe_load(server_config_path().read_text()) or {}


def migrate(*args):
    return CliRunner().invoke(cli, ["user", "token", "migrate", *args])


# --- acceptance --------------------------------------------------------------

def test_migrate_moves_tokens_into_the_store_and_out_of_server_yaml(ann):
    _write_server_yaml(bind="0.0.0.0", port=9000, tokens=_entries())

    result = migrate("--to", "ann")
    assert result.exit_code == 0, result.output
    assert "laptop" in result.output and "ops" in result.output

    with open_store() as store:
        tokens = {token.name: token for token in store.list_tokens("ann")}
    assert set(tokens) == {"laptop", "ops"}
    assert tokens["laptop"].hash == hash_token("reader-secret")
    assert tokens["laptop"].scope == {"*": "reader"}
    assert tokens["laptop"].last4 is None
    assert tokens["ops"].hash == hash_token("admin-secret")
    assert tokens["ops"].scope == {"*": "admin"}
    assert tokens["ops"].last4 is None

    # The old reader secret still authenticates, now through the store.
    with open_store() as store:
        token = accounts.token_for(store, "reader-secret", now=1_000_000.0)
    assert token is not None
    assert token.owner == "ann"
    assert token.scope == {"*": "reader"}

    saved = _read_server_yaml()
    assert "tokens" not in saved
    assert saved["bind"] == "0.0.0.0"
    assert saved["port"] == 9000

    again = migrate("--to", "ann")
    assert again.exit_code == 0, again.output
    assert "nothing to import" in again.output.lower()
    with open_store() as store:
        assert len(store.list_tokens("ann")) == 2


def test_migrate_to_an_unknown_user_is_refused(config_dir):
    _write_server_yaml(port=9000, tokens=_entries())
    result = migrate("--to", "nobody")
    assert result.exit_code != 0
    assert "nobody" in result.output
    assert _read_server_yaml()["tokens"] == _entries()


def test_migrate_needs_to(ann):
    _write_server_yaml(tokens=_entries())
    result = migrate()
    assert result.exit_code != 0


def test_serve_refuses_with_tokens_in_server_yaml(tmp_config, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    _write_server_yaml(port=9000, tokens=_entries())

    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code != 0
    assert MIGRATE in result.output
    assert calls == []


def test_serve_starts_with_no_tokens_in_server_yaml(tmp_config, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    _write_server_yaml(port=9000, tokens=[])

    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][1]["port"] == 9000
    assert "awiki token add" not in result.output


# --- more of the command -----------------------------------------------------

def test_migrate_with_no_server_yaml_has_nothing_to_import(ann):
    result = migrate("--to", "ann")
    assert result.exit_code == 0, result.output
    assert "nothing to import" in result.output.lower()
    assert not server_config_path().exists()


def test_migrate_with_an_empty_tokens_list_drops_the_key(ann):
    _write_server_yaml(bind="127.0.0.1", tokens=[])
    result = migrate("--to", "ann")
    assert result.exit_code == 0, result.output
    assert _read_server_yaml() == {"bind": "127.0.0.1"}


def test_migrate_takes_the_user_name_in_any_case(ann):
    _write_server_yaml(tokens=_entries())
    result = migrate("--to", "ANN")
    assert result.exit_code == 0, result.output
    with open_store() as store:
        assert len(store.list_tokens("ann")) == 2


def test_a_name_clash_is_skipped_reported_and_kept_in_server_yaml(ann):
    with open_store() as store:
        accounts.mint_token(store, "ann", "laptop", None)
    _write_server_yaml(port=9000, tokens=_entries())

    result = migrate("--to", "ann")
    assert result.exit_code == 0, result.output
    assert "laptop" in result.output
    assert "already has a token named" in result.output

    with open_store() as store:
        tokens = {token.name: token for token in store.list_tokens("ann")}
    assert tokens["laptop"].hash != hash_token("reader-secret")
    assert tokens["ops"].hash == hash_token("admin-secret")
    # The token not carried over stays, so its secret is not lost.
    saved = _read_server_yaml()
    assert saved["tokens"] == [_entries()[0]]
    assert saved["port"] == 9000


def test_migrate_refuses_a_malformed_tokens_value(ann):
    _write_server_yaml(tokens="not a list")
    result = migrate("--to", "ann")
    assert result.exit_code != 0
    assert _read_server_yaml()["tokens"] == "not a list"


def test_migrated_token_lists_as_legacy(ann):
    _write_server_yaml(tokens=_entries())
    assert migrate("--to", "ann").exit_code == 0
    result = CliRunner().invoke(cli, ["user", "token", "list", "ann"])
    assert result.exit_code == 0, result.output
    assert "laptop  legacy" in result.output
    assert "scope: *=reader" in result.output


# --- accounts.migrate_server_tokens ------------------------------------------

def test_migrate_server_tokens_returns_imported_and_skipped(ann):
    with open_store() as store:
        imported, skipped = accounts.migrate_server_tokens(
            store, "ann", _entries(), now="2026-01-02T03:04:05Z")
        assert imported == ["laptop", "ops"]
        assert skipped == []
        [token] = [t for t in store.list_tokens("ann") if t.name == "ops"]
        assert token.created == "2026-01-02T03:04:05Z"
        assert token.last_used is None

        imported, skipped = accounts.migrate_server_tokens(store, "ann", _entries())
        assert imported == [] and skipped == []
        assert len(store.list_tokens("ann")) == 2


def test_migrate_server_tokens_skips_a_hash_another_user_holds(ann):
    with open_store() as store:
        accounts.add_user(store, "bob", None)
        store.add_token(Token(owner="bob", name="old", hash=hash_token("reader-secret"),
                              last4=None, scope=None, created="2026-01-01T00:00:00Z"))
        imported, skipped = accounts.migrate_server_tokens(store, "ann", _entries())
        assert imported == ["ops"]
        assert skipped == []
        assert [t.name for t in store.list_tokens("ann")] == ["ops"]


def test_migrate_server_tokens_refuses_an_unknown_user(config_dir):
    with open_store() as store:
        with pytest.raises(AccountError):
            accounts.migrate_server_tokens(store, "nobody", _entries())


@pytest.mark.parametrize("bad", [
    {"role": "reader", "hash": "a" * 64},
    {"name": "", "role": "reader", "hash": "a" * 64},
    {"name": "x", "role": "owner", "hash": "a" * 64},
    {"name": "x", "role": "none", "hash": "a" * 64},
    {"name": "x", "hash": "a" * 64},
    {"name": "x", "role": "reader"},
    {"name": "x", "role": "reader", "hash": "abc"},
    {"name": "x", "role": "reader", "hash": "g" * 64},
    "not a mapping",
])
def test_migrate_server_tokens_refuses_a_malformed_entry_before_writing(ann, bad):
    with open_store() as store:
        with pytest.raises(AccountError):
            accounts.migrate_server_tokens(store, "ann", _entries() + [bad])
        assert store.list_tokens("ann") == []


def test_imported_hash_matches_the_store_hash():
    assert hash_token("some-secret") == hash_secret("some-secret")
