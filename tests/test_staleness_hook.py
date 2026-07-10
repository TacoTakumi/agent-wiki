"""Tests for the check_stale startup hook wired into the cli() group callback.

The hook (AgentSquire's check_stale) runs before any subcommand dispatch. Under
CliRunner stdin/stderr are never TTYs, so every invocation here exercises the
NON-interactive (agent) path: at most one stderr notice, stdout untouched, exit
code unchanged. We drive a benign, staleness-independent command (`awiki search`
with a no-match query → "No results found." / exit 0) so the only thing that can
vary between a stale and a fresh install is the hook's stderr line.

Fixture injection mirrors the CLI's own resolution: the hook reads Path.home()
for the user root and Path.cwd() for the project root, so we patch Path.home to
a fixture home and chdir into a fixture project, each carrying a .claude marker
so the claude-code harness is detected.
"""

import shutil
from pathlib import Path

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
# The hook's non-TTY notice line signature (agentsquire.staleness.check_stale).
NOTICE_MARK = "a new version is available"


@pytest.fixture
def harness_home(tmp_path, monkeypatch):
    """A fixture home + project, each with a claude-code (.claude) marker, wired
    so the hook resolves them. Returns the user-scope skills dir root (home)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)
    return home, project


def _notice_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if NOTICE_MARK in ln]


def _install_stale(home, project, tmp_path):
    """Install a MODIFIED copy of one bundled skill so its provenance stamp
    records a content hash that differs from the (unmodified) bundled copy —
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
    """A stale install (non-TTY): exactly one stderr notice naming the update,
    while stdout and exit code are exactly those of the benign command."""
    home, project = harness_home
    _install_stale(home, project, tmp_path)

    result = CliRunner().invoke(cli, ["search", NO_MATCH_QUERY])

    # REQ-07: exit code and stdout are the command's own, untouched by the hook.
    # (result.stdout is stdout only; the hook writes solely to result.stderr.)
    assert result.exit_code == 0, result.output
    assert result.stdout == NO_RESULTS_STDOUT
    # REQ-08: exactly one notice line, and it names the package, the stale skill,
    # and the skills-update command the reader/agent should run.
    notices = _notice_lines(result.stderr)
    assert len(notices) == 1, result.stderr
    line = notices[0]
    assert "agent_wiki" in line
    assert "awiki-search" in line
    assert "skills update" in line


def test_fresh_install_emits_no_notice(harness_home, tmp_config):
    """A fresh (up-to-date) install: no staleness notice at all, same stdout and
    exit code as the stale case — proving stdout is byte-identical either way."""
    home, project = harness_home
    _install_fresh(home, project)

    result = CliRunner().invoke(cli, ["search", NO_MATCH_QUERY])

    assert result.exit_code == 0, result.output
    assert result.stdout == NO_RESULTS_STDOUT
    assert _notice_lines(result.stderr) == []
