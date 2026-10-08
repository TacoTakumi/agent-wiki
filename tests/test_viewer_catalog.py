"""Viewer catalog: which vaults the viewer serves, built from the registry or
a vault override, with remote entries skipped and missing paths unavailable."""

from pathlib import Path

from agent_wiki.registry import VaultEntry
from agent_wiki.viewer.catalog import build_vault_catalog


def _registry(tmp_path):
    """Two local vaults that exist, one remote, one local whose path is gone."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    return {
        "alpha": VaultEntry(name="alpha", path=tmp_path / "alpha"),
        "beta": VaultEntry(name="beta", path=tmp_path / "beta"),
        "team": VaultEntry(name="team", url="https://wiki.example.com",
                           token="tok-1"),
        "gone": VaultEntry(name="gone", path=tmp_path / "gone"),
    }


def test_registry_serves_local_vaults_by_name(tmp_path):
    catalog = build_vault_catalog(_registry(tmp_path))

    assert catalog.vaults == {
        "alpha": tmp_path / "alpha",
        "beta": tmp_path / "beta",
    }
    assert catalog.skipped_remote == 1
    assert catalog.unavailable == ("gone",)


def test_served_vaults_are_sorted_by_name(tmp_path):
    (tmp_path / "z").mkdir()
    (tmp_path / "a").mkdir()
    registry = {
        "zed": VaultEntry(name="zed", path=tmp_path / "z"),
        "ant": VaultEntry(name="ant", path=tmp_path / "a"),
    }

    assert list(build_vault_catalog(registry).vaults) == ["ant", "zed"]


def test_entry_with_both_url_and_path_counts_as_remote(tmp_path):
    # A legacy config may carry vault_path and server together; url wins at
    # backend selection, so the viewer treats the entry as remote too.
    (tmp_path / "v").mkdir()
    registry = {
        "main": VaultEntry(name="main", path=tmp_path / "v",
                           url="https://wiki.example.com", legacy=True),
    }
    catalog = build_vault_catalog(registry)

    assert catalog.vaults == {}
    assert catalog.skipped_remote == 1


def test_empty_registry_serves_nothing():
    catalog = build_vault_catalog({})

    assert catalog.vaults == {}
    assert catalog.skipped_remote == 0
    assert catalog.unavailable == ()


def test_path_override_serves_one_vault_named_local(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    override = VaultEntry(name=str(scratch), path=scratch)

    catalog = build_vault_catalog(_registry(tmp_path), override)

    assert catalog.vaults == {"local": scratch}
    assert catalog.skipped_remote == 0
    assert catalog.unavailable == ()


def test_bare_word_path_override_not_in_registry_is_named_local(tmp_path):
    # A name-eligible value that matches no configured vault resolves as a
    # path, so it is served as local, not under the raw value.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    override = VaultEntry(name="scratch", path=scratch)

    catalog = build_vault_catalog(_registry(tmp_path), override)

    assert catalog.vaults == {"local": scratch}


def test_name_override_serves_that_vault_only(tmp_path):
    registry = _registry(tmp_path)

    catalog = build_vault_catalog(registry, registry["beta"])

    assert catalog.vaults == {"beta": tmp_path / "beta"}
    assert catalog.skipped_remote == 0
    assert catalog.unavailable == ()


def test_remote_override_serves_no_vaults(tmp_path):
    registry = _registry(tmp_path)

    catalog = build_vault_catalog(registry, registry["team"])

    assert catalog.vaults == {}
    assert catalog.skipped_remote == 1
    assert catalog.unavailable == ()


def test_name_override_with_missing_path_is_unavailable(tmp_path):
    registry = _registry(tmp_path)

    catalog = build_vault_catalog(registry, registry["gone"])

    assert catalog.vaults == {}
    assert catalog.unavailable == ("gone",)


def test_catalog_reads_no_config(tmp_path, monkeypatch):
    # Pure with respect to its inputs: an override in the environment and a
    # config dir pointing nowhere change nothing.
    monkeypatch.setenv("AWIKI_VAULT", "/nonexistent")
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path / "no-config"))
    registry = _registry(tmp_path)

    catalog = build_vault_catalog(registry)

    assert set(catalog.vaults) == {"alpha", "beta"}


def test_load_vault_catalog_reads_registry_and_override(tmp_config, tmp_vault,
                                                        tmp_path, monkeypatch):
    from agent_wiki.viewer.catalog import load_vault_catalog

    monkeypatch.delenv("AWIKI_VAULT", raising=False)
    assert load_vault_catalog().vaults == {"main": Path(tmp_vault)}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("AWIKI_VAULT", str(scratch))
    assert load_vault_catalog().vaults == {"local": scratch}
