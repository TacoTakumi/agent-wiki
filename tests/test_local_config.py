"""Context-local .agent-wiki/config.yaml: walk-up discovery bounded by $HOME,
direnv-style trust gating with a one-line notice, and additive merge over the
global config with local winning on name collision (T-05)."""

import yaml
import pytest

from conftest import make_vault


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))
    return path


def _setup(tmp_path, monkeypatch, *, global_config, trust=()):
    """Isolated HOME + global config dir; returns the fake home dir."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    config_dir = tmp_path / "global-config"
    global_config = dict(global_config)
    if trust:
        global_config["trusted_dirs"] = [str(d) for d in trust]
    _write_yaml(config_dir / "config.yaml", global_config)
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return home


def test_nearest_trusted_local_config_merges_additively(tmp_path, monkeypatch):
    from agent_wiki.config import load_registry

    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    ancestor = make_vault(tmp_path / "ancestor")

    work = tmp_path / "home" / "work"
    proj = work / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
        trust=(proj, work),
    )
    # A farther ancestor local config (also trusted) must be ignored: nearest only.
    _write_yaml(
        work / ".agent-wiki" / "config.yaml",
        {"vaults": {"ancestor": {"path": str(ancestor)}}},
    )
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    nested = proj / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    registry = load_registry()
    assert set(registry) == {"main", "personal"}
    assert registry["personal"].path == personal


def test_local_wins_on_name_collision(tmp_path, monkeypatch):
    from agent_wiki.config import load_registry

    g_main = make_vault(tmp_path / "g-main")
    l_main = make_vault(tmp_path / "l-main")
    proj = tmp_path / "home" / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
        trust=(proj,),
    )
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"main": {"path": str(l_main)}}},
    )
    monkeypatch.chdir(proj)

    assert load_registry()["main"].path == l_main


def test_local_default_vault_beats_global(tmp_path, monkeypatch):
    from agent_wiki.config import get_vault_path

    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    proj = tmp_path / "home" / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={
            "default_vault": "main",
            "vaults": {"main": {"path": str(g_main)}},
        },
        trust=(proj,),
    )
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {
            "default_vault": "personal",
            "vaults": {"personal": {"path": str(personal)}},
        },
    )
    monkeypatch.chdir(proj)

    assert get_vault_path() == personal


def test_untrusted_local_config_is_ignored_with_one_notice(
    tmp_path, monkeypatch, capsys
):
    from agent_wiki.config import load_registry

    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    proj = tmp_path / "home" / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
    )
    local_file = _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    monkeypatch.chdir(proj)

    registry = load_registry()
    assert set(registry) == {"main"}

    err = capsys.readouterr().err
    notice_lines = [line for line in err.splitlines() if line.strip()]
    assert len(notice_lines) == 1
    assert str(local_file) in notice_lines[0]
    assert "vault trust" in notice_lines[0]


def test_outside_home_tree_no_local_discovery(tmp_path, monkeypatch, capsys):
    from agent_wiki.config import load_registry

    g_main = make_vault(tmp_path / "g-main")
    elsewhere = tmp_path / "elsewhere" / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
        trust=(elsewhere,),
    )
    _write_yaml(
        elsewhere / ".agent-wiki" / "config.yaml",
        {"vaults": {"rogue": {"path": str(tmp_path / "rogue")}}},
    )
    monkeypatch.chdir(elsewhere)

    assert set(load_registry()) == {"main"}
    assert capsys.readouterr().err == ""


def test_fresh_config_dir_starts_with_empty_allowlist(tmp_path, monkeypatch):
    """Under a fresh isolated AGENT_WIKI_CONFIG_DIR no local config is honored,
    even one whose directory some other config trusted."""
    from agent_wiki.config import load_registry

    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    proj = tmp_path / "home" / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
    )
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    monkeypatch.chdir(proj)

    assert set(load_registry()) == {"main"}


def test_no_local_config_is_global_only(tmp_path, monkeypatch, capsys):
    from agent_wiki.config import load_registry

    g_main = make_vault(tmp_path / "g-main")
    proj = tmp_path / "home" / "proj"
    proj.mkdir(parents=True)
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
    )
    monkeypatch.chdir(proj)

    assert set(load_registry()) == {"main"}
    assert capsys.readouterr().err == ""


def test_name_override_sees_local_vaults(tmp_path, monkeypatch):
    """REQ-06: --vault/AWIKI_VAULT name lookup consults the merged view."""
    from agent_wiki.config import get_vault_path

    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    proj = tmp_path / "home" / "proj"
    _setup(
        tmp_path, monkeypatch,
        global_config={"vaults": {"main": {"path": str(g_main)}}},
        trust=(proj,),
    )
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    monkeypatch.chdir(proj)
    monkeypatch.setenv("AWIKI_VAULT", "personal")

    assert get_vault_path() == personal
