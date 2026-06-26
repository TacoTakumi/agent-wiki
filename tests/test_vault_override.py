"""Vault override (--vault / AWIKI_VAULT) and stale-config diagnostics.

The override lets `awiki` target a vault other than the configured one for a
single invocation — the headless-Hermes escape hatch when ~/.config is stale.
Precedence: --vault (CLI) > AWIKI_VAULT (env) > configured vault_path.
An override always forces a *local* vault (a configured remote server is ignored).
"""

import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.config import get_backend, get_vault_path
from agent_wiki.remote import RemoteVaultService
from agent_wiki.service import LocalVaultService


def _make_vault(path):
    """Materialize a minimal but valid vault at `path` and return it."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "wiki.yaml").write_text(yaml.dump({
        "vault": {"name": "Test Wiki", "version": 1},
        "topics": ["projects", "decisions", "research", "tools"],
        "default_topic": "research",
    }))
    (path / "raw").mkdir()
    (path / "index.md").write_text("# Index\n")
    (path / "log.md").write_text("# Activity Log\n")
    for topic in ("projects", "decisions", "research", "tools"):
        (path / topic).mkdir()
    return path


def _write_config(config_dir, config):
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg = config_dir / "config.yaml"
    cfg.write_text(yaml.dump(config))
    return cfg


@pytest.fixture
def isolated_env(monkeypatch):
    """Clear the env knobs so each test sets only what it means to."""
    monkeypatch.delenv("AWIKI_VAULT", raising=False)
    monkeypatch.delenv("AGENT_WIKI_CONFIG_DIR", raising=False)
    return monkeypatch


# --- resolution precedence (config layer) ------------------------------------

def test_awiki_vault_env_overrides_configured_vault(tmp_path, isolated_env):
    configured = _make_vault(tmp_path / "configured")
    override = _make_vault(tmp_path / "override")
    config_dir = tmp_path / "config"
    _write_config(config_dir, {"vault_path": str(configured)})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    isolated_env.setenv("AWIKI_VAULT", str(override))

    assert get_vault_path() == override


def test_awiki_vault_override_to_missing_path_is_friendly_error(tmp_path, isolated_env):
    config_dir = tmp_path / "config"
    _write_config(config_dir, {"vault_path": str(_make_vault(tmp_path / "ok"))})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    isolated_env.setenv("AWIKI_VAULT", str(tmp_path / "ghost"))

    import click
    with pytest.raises(click.UsageError) as exc:
        get_vault_path()
    msg = str(exc.value)
    assert str(tmp_path / "ghost") in msg
    assert "AWIKI_VAULT" in msg or "--vault" in msg


def test_stale_configured_vault_names_config_file_and_key(tmp_path, isolated_env):
    config_dir = tmp_path / "config"
    cfg = _write_config(config_dir, {"vault_path": str(tmp_path / "gone")})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    import click
    with pytest.raises(click.UsageError) as exc:
        get_vault_path()
    msg = str(exc.value)
    assert str(cfg) in msg          # names the offending config file
    assert "vault_path" in msg      # names the offending key
    assert "--vault" in msg         # offers the escape hatch


def test_override_forces_local_vault_over_configured_server(tmp_path, isolated_env):
    override = _make_vault(tmp_path / "override")
    config_dir = tmp_path / "config"
    _write_config(config_dir, {
        "vault_path": str(_make_vault(tmp_path / "configured")),
        "server": {"url": "http://example.invalid", "token": "t"},
    })
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    isolated_env.setenv("AWIKI_VAULT", str(override))

    backend = get_backend()
    assert isinstance(backend, LocalVaultService)
    assert not isinstance(backend, RemoteVaultService)


# --- --vault flag (CLI layer) ------------------------------------------------

def test_vault_flag_rescues_a_stale_config(tmp_path, isolated_env):
    good = _make_vault(tmp_path / "good")
    config_dir = tmp_path / "config"
    _write_config(config_dir, {"vault_path": str(tmp_path / "gone")})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    result = CliRunner().invoke(cli, ["--vault", str(good), "status"])
    assert result.exit_code == 0, result.output


def test_vault_flag_beats_awiki_vault_env(tmp_path, isolated_env):
    flag_vault = _make_vault(tmp_path / "flag")
    config_dir = tmp_path / "config"
    _write_config(config_dir, {"vault_path": str(tmp_path / "gone")})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    isolated_env.setenv("AWIKI_VAULT", str(tmp_path / "also-gone"))

    # The env points at a missing vault; the flag (which wins) points at a good one.
    result = CliRunner().invoke(cli, ["--vault", str(flag_vault), "status"])
    assert result.exit_code == 0, result.output


def test_plain_command_on_stale_config_is_friendly(tmp_path, isolated_env):
    config_dir = tmp_path / "config"
    cfg = _write_config(config_dir, {"vault_path": str(tmp_path / "gone")})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code != 0
    assert "vault_path" in result.output
    assert str(cfg) in result.output
    assert "--vault" in result.output


# --- doctor repairs a stale config -------------------------------------------

def test_doctor_repairs_stale_config_with_override(tmp_path, isolated_env):
    good = _make_vault(tmp_path / "good")
    config_dir = tmp_path / "config"
    cfg = _write_config(config_dir, {"vault_path": str(tmp_path / "gone")})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    result = CliRunner().invoke(cli, ["--vault", str(good), "doctor", "--fix"])
    assert result.exit_code == 0, result.output
    persisted = yaml.safe_load(cfg.read_text())
    assert persisted["vault_path"] == str(good)


def test_doctor_stale_config_without_override_errors(tmp_path, isolated_env):
    config_dir = tmp_path / "config"
    cfg = _write_config(config_dir, {"vault_path": str(tmp_path / "gone")})
    isolated_env.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code != 0
    assert str(cfg) in result.output
    assert "--vault" in result.output
