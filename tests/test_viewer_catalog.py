"""Viewer catalog: which vaults the viewer serves, built from the registry or
a vault override, with remote entries skipped and missing paths unavailable."""

import pytest
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


# --- per-vault page index and safe URL resolution ---

def _write_page(path, title, **meta):
    from agent_wiki.page import render_page
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_page({"title": title, **meta}, f"# {title}\n"))
    return path


def test_index_lists_topic_pages_with_metadata(tmp_vault):
    from agent_wiki.viewer.catalog import index_vault

    page = _write_page(tmp_vault / "research" / "alpha.md", "Alpha",
                       topic="research", tags=["x", "y"],
                       created="2026-01-01", updated="2026-02-01")

    index = index_vault(tmp_vault)

    [record] = index.topics["research"]
    assert record.title == "Alpha"
    assert record.topic == "research"
    assert record.tags == ("x", "y")
    assert record.created == "2026-01-01"
    assert record.updated == "2026-02-01"
    assert record.url_path == "research/alpha"
    assert record.path == page


def test_index_keeps_wiki_yaml_topic_order_and_empty_topic_folders(tmp_vault):
    from agent_wiki.viewer.catalog import index_vault

    _write_page(tmp_vault / "tools" / "t.md", "T")
    (tmp_vault / "projects").rmdir()  # a topic with no folder is not listed

    index = index_vault(tmp_vault)

    assert list(index.topics) == ["decisions", "research", "tools"]
    assert index.topics["decisions"] == ()


def test_index_uses_the_rebuild_index_page_rule(tmp_vault):
    from agent_wiki.viewer.catalog import index_vault

    _write_page(tmp_vault / "research" / "nested" / "deep.md", "Deep")
    (tmp_vault / "research" / "bare.md").write_text("# no frontmatter\n")
    (tmp_vault / "research" / "notes.txt").write_text("---\ntitle: Txt\n---\n")
    _write_page(tmp_vault / "elsewhere" / "stray.md", "Stray")
    _write_page(tmp_vault / "raw" / "r.md", "Raw")
    (tmp_vault / "research" / "untitled.md").write_text("---\ntags: [a]\n---\n")

    index = index_vault(tmp_vault)

    assert [r.url_path for r in index.topics["research"]] == [
        "research/nested/deep", "research/untitled"]
    # A page with frontmatter but no title falls back to its file stem.
    assert index.topics["research"][1].title == "untitled"
    assert index.topics["research"][1].created == ""


def test_index_normalises_yaml_dates_to_strings(tmp_vault):
    from agent_wiki.viewer.catalog import index_vault

    (tmp_vault / "research" / "d.md").write_text(
        "---\ntitle: D\ncreated: 2026-03-04\nupdated: 2026-03-05\n---\n")

    [record] = index_vault(tmp_vault).topics["research"]

    assert (record.created, record.updated) == ("2026-03-04", "2026-03-05")


def test_index_builds_title_to_url_map(tmp_vault):
    from agent_wiki.viewer.catalog import index_vault

    _write_page(tmp_vault / "research" / "a.md", "Alpha")
    _write_page(tmp_vault / "tools" / "sub" / "b.md", "Beta")

    assert index_vault(tmp_vault).titles == {
        "Alpha": "research/a",
        "Beta": "tools/sub/b",
    }


def test_index_skips_a_page_symlinked_out_of_the_vault(tmp_vault, tmp_path):
    from agent_wiki.viewer.catalog import index_vault

    outside = _write_page(tmp_path / "outside" / "secret.md", "Secret")
    (tmp_vault / "research" / "secret.md").symlink_to(outside)

    index = index_vault(tmp_vault)

    assert index.topics["research"] == ()
    assert "Secret" not in index.titles


def test_resolve_page_finds_topic_pages(tmp_vault):
    from agent_wiki.viewer.catalog import resolve_page

    page = _write_page(tmp_vault / "research" / "alpha.md", "Alpha")
    nested = _write_page(tmp_vault / "research" / "sub" / "deep.md", "Deep")

    assert resolve_page(tmp_vault, "research/alpha") == page
    assert resolve_page(tmp_vault, "research/sub/deep") == nested


def test_resolve_page_refuses_everything_outside_topic_folders(tmp_vault,
                                                               tmp_path):
    from agent_wiki.viewer.catalog import resolve_page

    (tmp_vault / "raw" / "r.md").write_text("raw\n")
    _write_page(tmp_vault / "elsewhere" / "stray.md", "Stray")
    (tmp_vault / "research" / "notes.txt").write_text("text\n")
    _write_page(tmp_vault / "research" / "alpha.md", "Alpha")
    outside = _write_page(tmp_path / "outside" / "secret.md", "Secret")
    (tmp_vault / "research" / "secret.md").symlink_to(outside)
    (tmp_vault / "research" / "rawlink.md").symlink_to(
        tmp_vault / "raw" / "r.md")

    for url_path in [
        "raw/r", "raw/r.md", "wiki.yaml", "wiki", "log", "log.md", "index",
        "index.md", "elsewhere/stray", "research/notes.txt", "research/notes",
        "research/alpha.md", "research", "research/", "research/missing",
        "research/../research/alpha", "research/../../etc/passwd",
        "../vault/research/alpha", "/research/alpha", "research//alpha",
        "research/./alpha", "research/secret", "research/rawlink", "",
        "research/al\0pha",
    ]:
        assert resolve_page(tmp_vault, url_path) is None, url_path


# A page whose frontmatter cannot be read is left out, so one bad file never
# takes the rest of the vault down with it.
BAD_FRONTMATTER = {
    "malformed.md": b"---\ntitle: [unclosed\n---\nbody\n",
    "listblock.md": b"---\n- a\n- b\n---\nbody\n",
    "stringblock.md": b"---\njust a string\n---\nbody\n",
    "latin1.md": "---\ntitle: Caf\xe9\n---\nbody\n".encode("latin-1"),
}


@pytest.mark.parametrize("filename", sorted(BAD_FRONTMATTER))
def test_index_skips_page_with_unreadable_frontmatter(tmp_vault, filename):
    from agent_wiki.viewer.catalog import index_vault
    _write_page(tmp_vault / "research" / "good.md", "Good")
    (tmp_vault / "research" / filename).write_bytes(BAD_FRONTMATTER[filename])
    index = index_vault(tmp_vault)
    assert [rec.title for rec in index.topics["research"]] == ["Good"]
    assert index.titles == {"Good": "research/good"}


def test_index_skips_page_it_cannot_read(tmp_vault):
    import os
    from agent_wiki.viewer.catalog import index_vault
    _write_page(tmp_vault / "research" / "good.md", "Good")
    locked = _write_page(tmp_vault / "research" / "locked.md", "Locked")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with permission to read any file")
        index = index_vault(tmp_vault)
    finally:
        locked.chmod(0o644)
    assert [rec.title for rec in index.topics["research"]] == ["Good"]
