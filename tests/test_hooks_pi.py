"""Structural tests for the pi hook backend (no pi runtime involved)."""
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.hooks import BACKENDS
from agent_wiki.hooks import pi as pi_backend


@pytest.fixture
def pi_dir(tmp_path, monkeypatch) -> Path:
    agent_dir = tmp_path / "pi-agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    return agent_dir


def test_backends_include_pi():
    assert "pi" in BACKENDS


def test_install_writes_marker_tagged_extension(pi_dir):
    msg = pi_backend.install()
    ext = pi_dir / "extensions" / "awiki-sync.ts"
    assert ext.exists()
    assert str(ext) in msg
    text = ext.read_text()
    assert pi_backend.MARKER in text
    assert "session_start" in text
    assert "pi.exec" in text
    assert '"sync"' in text and '"--detach"' in text
    assert "try" in text and "catch" in text
    assert "timeout" in text


def test_install_is_byte_identical_on_rerun(pi_dir):
    pi_backend.install()
    ext = pi_dir / "extensions" / "awiki-sync.ts"
    first = ext.read_bytes()
    msg = pi_backend.install()
    assert ext.read_bytes() == first
    assert "already" in msg.lower()


def test_uninstall_removes_file_and_repeats_as_noop(pi_dir):
    pi_backend.install()
    ext = pi_dir / "extensions" / "awiki-sync.ts"
    assert "uninstalled" in pi_backend.uninstall().lower()
    assert not ext.exists()
    assert "nothing" in pi_backend.uninstall().lower()


def test_uninstall_refuses_to_delete_a_foreign_file(pi_dir):
    ext = pi_dir / "extensions" / "awiki-sync.ts"
    ext.parent.mkdir(parents=True)
    ext.write_text("export default function () {}\n")
    msg = pi_backend.uninstall()
    assert ext.exists()
    assert "not awiki-managed" in msg.lower()


def test_status_reports_presence(pi_dir):
    assert "not installed" in pi_backend.status().lower()
    pi_backend.install()
    msg = pi_backend.status()
    assert "installed" in msg.lower() and "not installed" not in msg.lower()
    assert "session_start" in msg


def test_only_context_is_a_clear_noop(pi_dir):
    msg = pi_backend.install(only="context")
    assert not (pi_dir / "extensions" / "awiki-sync.ts").exists()
    assert "context" in msg.lower() and "no" in msg.lower()
    msg = pi_backend.uninstall(only="context")
    assert "context" in msg.lower()


def test_only_sweep_installs(pi_dir):
    pi_backend.install(only="sweep")
    assert (pi_dir / "extensions" / "awiki-sync.ts").exists()


def test_config_path_overrides_target_file(tmp_path, pi_dir):
    target = tmp_path / "elsewhere" / "custom.ts"
    pi_backend.install(config_path=target)
    assert target.exists()
    assert not (pi_dir / "extensions" / "awiki-sync.ts").exists()
    assert "installed" in pi_backend.status(config_path=target).lower()
    pi_backend.uninstall(config_path=target)
    assert not target.exists()


def test_cli_hook_install_pi(pi_dir):
    runner = CliRunner()
    result = runner.invoke(cli, ["hook", "install", "--agent", "pi"])
    assert result.exit_code == 0, result.output
    assert (pi_dir / "extensions" / "awiki-sync.ts").exists()
    result = runner.invoke(cli, ["hook", "status", "--agent", "pi"])
    assert result.exit_code == 0
    assert "installed" in result.output.lower()
    result = runner.invoke(cli, ["hook", "uninstall", "--agent", "pi"])
    assert result.exit_code == 0, result.output
    assert not (pi_dir / "extensions" / "awiki-sync.ts").exists()
