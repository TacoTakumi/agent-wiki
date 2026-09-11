"""Structural tests for the OpenCode hook backend (no OpenCode runtime involved)."""
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.hooks import BACKENDS
from agent_wiki.hooks import opencode as oc_backend


@pytest.fixture
def oc_dir(tmp_path, monkeypatch) -> Path:
    xdg = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg / "opencode" / "plugins"


def test_backends_include_opencode():
    assert "opencode" in BACKENDS


def test_install_writes_marker_tagged_plugin(oc_dir):
    msg = oc_backend.install()
    plugin = oc_dir / "awiki-sync.js"
    assert plugin.exists()
    assert str(plugin) in msg
    text = plugin.read_text()
    assert oc_backend.MARKER in text
    assert "session.created" in text
    assert "sync --detach" in text
    assert "try" in text and "catch" in text
    assert "nothrow" in text


def test_install_is_byte_identical_on_rerun(oc_dir):
    oc_backend.install()
    plugin = oc_dir / "awiki-sync.js"
    first = plugin.read_bytes()
    msg = oc_backend.install()
    assert plugin.read_bytes() == first
    assert "already" in msg.lower()


def test_uninstall_removes_file_and_repeats_as_noop(oc_dir):
    oc_backend.install()
    plugin = oc_dir / "awiki-sync.js"
    assert "uninstalled" in oc_backend.uninstall().lower()
    assert not plugin.exists()
    assert "nothing" in oc_backend.uninstall().lower()


def test_uninstall_refuses_to_delete_a_foreign_file(oc_dir):
    plugin = oc_dir / "awiki-sync.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export const X = async () => ({})\n")
    msg = oc_backend.uninstall()
    assert plugin.exists()
    assert "not awiki-managed" in msg.lower()


def test_status_reports_presence(oc_dir):
    assert "not installed" in oc_backend.status().lower()
    oc_backend.install()
    msg = oc_backend.status()
    assert "installed" in msg.lower() and "not installed" not in msg.lower()
    assert "session.created" in msg


def test_only_context_is_a_clear_noop(oc_dir):
    msg = oc_backend.install(only="context")
    assert not (oc_dir / "awiki-sync.js").exists()
    assert "context" in msg.lower() and "no" in msg.lower()


def test_config_path_overrides_target_file(tmp_path, oc_dir):
    target = tmp_path / "elsewhere" / "custom.js"
    oc_backend.install(config_path=target)
    assert target.exists()
    assert not (oc_dir / "awiki-sync.js").exists()
    oc_backend.uninstall(config_path=target)
    assert not target.exists()


def test_cli_hook_install_opencode(oc_dir):
    runner = CliRunner()
    result = runner.invoke(cli, ["hook", "install", "--agent", "opencode"])
    assert result.exit_code == 0, result.output
    assert (oc_dir / "awiki-sync.js").exists()
    result = runner.invoke(cli, ["hook", "status", "--agent", "opencode"])
    assert "installed" in result.output.lower()
    result = runner.invoke(cli, ["hook", "uninstall", "--agent", "opencode"])
    assert result.exit_code == 0, result.output
    assert not (oc_dir / "awiki-sync.js").exists()
