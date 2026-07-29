"""The awiki vault command group (registry management)."""

import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from conftest import make_vault


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))
    return path


def _setup_untrusted_local(tmp_path, monkeypatch):
    """A global config with one vault plus an untrusted local config declaring
    another; returns (global config file, project dir, local vault path)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(
        config_dir / "config.yaml",
        {"vaults": {"main": {"path": str(g_main)}}},
    )
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    proj = home / "proj"
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    monkeypatch.chdir(proj)
    return config_file, proj, personal


def test_vault_trust_records_dir_and_honors_local_config(tmp_path, monkeypatch):
    from agent_wiki.config import load_registry

    config_file, proj, personal = _setup_untrusted_local(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, ["vault", "trust", str(proj)])
    assert result.exit_code == 0, result.output

    persisted = yaml.safe_load(config_file.read_text())
    assert str(proj.resolve()) in persisted["trusted_dirs"]

    registry = load_registry()
    assert set(registry) == {"main", "personal"}
    assert registry["personal"].path == personal

    status = CliRunner().invoke(cli, ["status"])
    assert status.exit_code == 0, status.output
    assert "vault trust" not in status.stderr


def test_vault_trust_is_idempotent(tmp_path, monkeypatch):
    config_file, proj, _personal = _setup_untrusted_local(tmp_path, monkeypatch)

    runner = CliRunner()
    assert runner.invoke(cli, ["vault", "trust", str(proj)]).exit_code == 0
    assert runner.invoke(cli, ["vault", "trust", str(proj)]).exit_code == 0

    persisted = yaml.safe_load(config_file.read_text())
    assert persisted["trusted_dirs"].count(str(proj.resolve())) == 1


def test_vault_trust_missing_dir_errors(tmp_path, monkeypatch):
    _setup_untrusted_local(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, ["vault", "trust", str(tmp_path / "ghost")])
    assert result.exit_code != 0


def test_vault_trust_keeps_legacy_config_keys(tmp_path, monkeypatch):
    """Trusting must not rewrite a legacy config into the vaults: schema."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    vault_dir = make_vault(tmp_path / "legacy-vault")
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(
        config_dir / "config.yaml", {"vault_path": str(vault_dir)}
    )
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    proj = home / "proj"
    proj.mkdir()

    result = CliRunner().invoke(cli, ["vault", "trust", str(proj)])
    assert result.exit_code == 0, result.output

    persisted = yaml.safe_load(config_file.read_text())
    assert persisted["vault_path"] == str(vault_dir)
    assert "vaults" not in persisted
    assert str(proj.resolve()) in persisted["trusted_dirs"]
