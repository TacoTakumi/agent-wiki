"""Registry read layer: parsing the user config's vaults: map, with legacy
vault_path/server synthesis as a single vault named main (T-01)."""

import click
import pytest
import yaml

from agent_wiki.registry import VaultEntry, parse_registry


def test_vaults_map_parses_path_and_url_entries(tmp_path):
    config = {
        "vaults": {
            "work": {"path": str(tmp_path / "work-vault")},
            "team": {"url": "https://wiki.example.com", "token": "tok-1"},
        }
    }
    registry = parse_registry(config)

    assert set(registry) == {"work", "team"}

    work = registry["work"]
    assert work.name == "work"
    assert str(work.path) == str(tmp_path / "work-vault")
    assert work.url is None
    assert not work.is_remote

    team = registry["team"]
    assert team.name == "team"
    assert team.path is None
    assert team.url == "https://wiki.example.com"
    assert team.token == "tok-1"
    assert team.is_remote


def test_entry_with_neither_path_nor_url_errors_naming_entry():
    config = {"vaults": {"broken": {"token": "tok"}}}
    with pytest.raises(click.UsageError, match="broken"):
        parse_registry(config)


def test_entry_with_both_path_and_url_errors_naming_entry(tmp_path):
    config = {
        "vaults": {
            "confused": {"path": str(tmp_path), "url": "https://example.com"}
        }
    }
    with pytest.raises(click.UsageError, match="confused"):
        parse_registry(config)


def test_non_mapping_entry_errors_naming_entry():
    config = {"vaults": {"bare": "/some/path"}}
    with pytest.raises(click.UsageError, match="bare"):
        parse_registry(config)


def test_entry_path_expands_user():
    registry = parse_registry({"vaults": {"home": {"path": "~/vault"}}})
    assert "~" not in str(registry["home"].path)


def test_legacy_vault_path_synthesizes_main(tmp_path):
    registry = parse_registry({"vault_path": str(tmp_path / "vault")})

    assert set(registry) == {"main"}
    main = registry["main"]
    assert main.name == "main"
    assert str(main.path) == str(tmp_path / "vault")
    assert main.url is None
    assert not main.is_remote


def test_legacy_server_synthesizes_remote_main():
    config = {"server": {"url": "https://wiki.example.com", "token": "tok"}}
    registry = parse_registry(config)

    assert set(registry) == {"main"}
    main = registry["main"]
    assert main.url == "https://wiki.example.com"
    assert main.token == "tok"
    assert main.is_remote


def test_legacy_both_keys_synthesizes_main_with_both(tmp_path):
    """A legacy config may hold vault_path AND server; the synthesized main
    carries both so the existing precedence (server wins for the backend, the
    path serves local resolution) survives the rewire."""
    config = {
        "vault_path": str(tmp_path / "vault"),
        "server": {"url": "https://wiki.example.com", "token": "tok"},
    }
    registry = parse_registry(config)

    assert set(registry) == {"main"}
    main = registry["main"]
    assert str(main.path) == str(tmp_path / "vault")
    assert main.url == "https://wiki.example.com"
    assert main.is_remote


def test_legacy_server_without_url_is_not_remote(tmp_path):
    config = {"vault_path": str(tmp_path / "v"), "server": {"token": "tok"}}
    registry = parse_registry(config)
    assert registry["main"].url is None
    assert not registry["main"].is_remote


def test_vaults_map_wins_over_legacy_keys(tmp_path):
    config = {
        "vault_path": str(tmp_path / "old"),
        "vaults": {"work": {"path": str(tmp_path / "work")}},
    }
    registry = parse_registry(config)
    assert set(registry) == {"work"}


def test_empty_config_yields_empty_registry():
    assert parse_registry({}) == {}
    assert parse_registry(None) == {}


def test_load_registry_reads_user_config(two_vault_config):
    from agent_wiki.config import load_registry

    registry = load_registry()
    assert set(registry) == {"work", "personal"}
    assert registry["work"].path.exists()
    assert registry["personal"].path.exists()


def test_read_only_command_leaves_legacy_config_bytes_untouched(tmp_config):
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    before = tmp_config.read_bytes()
    result = CliRunner().invoke(cli, ["search", "anything"])
    assert result.exit_code == 0
    assert tmp_config.read_bytes() == before


def test_load_registry_leaves_config_bytes_untouched(tmp_config):
    from agent_wiki.config import load_registry

    before = tmp_config.read_bytes()
    registry = load_registry()
    assert set(registry) == {"main"}
    assert tmp_config.read_bytes() == before


def test_vault_entry_is_frozen():
    entry = VaultEntry(name="x", path=None, url="https://e.com", token=None)
    with pytest.raises(Exception):
        entry.name = "y"
