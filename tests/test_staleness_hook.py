"""Tests for the check_stale startup hook wired into the cli() group callback.

The hook (AgentSquire's check_stale) runs before any subcommand dispatch. It is
notice-only: with a stale install it prints exactly one stderr line naming the
update command, and it never prompts, reads stdin, writes stdout, or changes the
exit code. The notice is intentionally NOT gated on an interactive TTY, so an
agent running awiki with captured (non-TTY) stderr still sees it; only CI or
AGENTSQUIRE_NO_UPDATE_CHECK suppress it. We drive a benign,
staleness-independent command (`awiki search` with a no-match query ->
"No results found." / exit 0) so the only thing that varies between a stale and
a fresh install is the hook's stderr line.

Root injection uses AGENTSQUIRE_HOME / AGENTSQUIRE_PROJECT (agentsquire 0.2.1):
the wired cli() call omits home/project (production resolves the real roots), so
we point it at fixture directories with two env vars - no Path.home monkeypatch
and no chdir needed.
"""

import shutil

import pytest
from click.testing import CliRunner

from agent_wiki import __version__
from agent_wiki.cli import cli
from agentsquire import (
    CLAUDE_CODE,
    BundledPackageDataSource,
    DirectorySource,
    install,
)

# Benign, staleness-independent stdout: search with a query that matches nothing.
NO_MATCH_QUERY = "zznomatchtoken12345"
NO_RESULTS_STDOUT = "No results found.\n"
# The hook's notice line signature (agentsquire.staleness.check_stale).
NOTICE_MARK = "a skills update is available"


@pytest.fixture
def harness_home(tmp_path, monkeypatch):
    """A fixture home + project, each with a claude-code (.claude) marker, wired
    so the cli() hook resolves them via AGENTSQUIRE_HOME/AGENTSQUIRE_PROJECT (no
    Path.home monkeypatch, no chdir). CI / AGENTSQUIRE_NO_UPDATE_CHECK are cleared
    so the notice gate is open. Returns (home, project)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    monkeypatch.setenv("AGENTSQUIRE_HOME", str(home))
    monkeypatch.setenv("AGENTSQUIRE_PROJECT", str(project))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AGENTSQUIRE_NO_UPDATE_CHECK", raising=False)
    return home, project


def _notice_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if NOTICE_MARK in ln]


def _install_stale(home, project, tmp_path):
    """Install a MODIFIED copy of one bundled skill so its provenance stamp
    records a content hash that differs from the (unmodified) bundled copy -
    which classifies as UPDATE_AVAILABLE, not LOCALLY_MODIFIED."""
    staging = tmp_path / "staging"
    staging.mkdir()
    with BundledPackageDataSource("agent_wiki").materialize("awiki-search") as p:
        shutil.copytree(p, staging / "awiki-search")
    smd = staging / "awiki-search" / "SKILL.md"
    smd.write_text(smd.read_text() + "\n<!-- drift: forces a distinct hash -->\n")
    install(
        DirectorySource(staging), CLAUDE_CODE, scope="user",
        home=home, project=project,
        source_package="agent_wiki", source_version="0.0.0-old",
    )


def _install_fresh(home, project):
    """Install the actual bundled skills; stamp hash matches the shipped copy,
    so status is UP_TO_DATE for all of them."""
    install(
        BundledPackageDataSource("agent_wiki"), CLAUDE_CODE, scope="user",
        home=home, project=project,
        source_package="agent_wiki", source_version=__version__,
    )


def test_stale_install_emits_exactly_one_stderr_notice(harness_home, tmp_config, tmp_path):
    """A stale install: exactly one stderr notice naming the update, while stdout
    and exit code are exactly those of the benign command."""
    home, project = harness_home
    _install_stale(home, project, tmp_path)

    result = CliRunner().invoke(cli, ["search", NO_MATCH_QUERY])

    # REQ-07: exit code and stdout are the command's own, untouched by the hook.
    # (result.stdout is stdout only; the hook writes solely to result.stderr.)
    assert result.exit_code == 0, result.output
    assert result.stdout == NO_RESULTS_STDOUT
    # REQ-08: exactly one notice line, and it names the CLI (awiki, not the
    # package), the stale skill, and the exact skills-update command to run.
    notices = _notice_lines(result.stderr)
    assert len(notices) == 1, result.stderr
    line = notices[0]
    assert line.startswith("awiki:")
    assert "awiki-search" in line
    assert "awiki skills update" in line


def test_fresh_install_emits_no_notice(harness_home, tmp_config):
    """A fresh (up-to-date) install: no staleness notice at all, same stdout and
    exit code as the stale case - proving stdout is byte-identical either way."""
    home, project = harness_home
    _install_fresh(home, project)

    result = CliRunner().invoke(cli, ["search", NO_MATCH_QUERY])

    assert result.exit_code == 0, result.output
    assert result.stdout == NO_RESULTS_STDOUT
    assert _notice_lines(result.stderr) == []


def test_no_update_check_env_suppresses_the_notice(harness_home, tmp_config, tmp_path, monkeypatch):
    """Even with a stale install, AGENTSQUIRE_NO_UPDATE_CHECK silences the notice
    - the escape hatch a consumer's own test suite / CI uses."""
    home, project = harness_home
    _install_stale(home, project, tmp_path)
    monkeypatch.setenv("AGENTSQUIRE_NO_UPDATE_CHECK", "1")

    result = CliRunner().invoke(cli, ["search", NO_MATCH_QUERY])

    assert result.exit_code == 0, result.output
    assert result.stdout == NO_RESULTS_STDOUT
    assert _notice_lines(result.stderr) == []
