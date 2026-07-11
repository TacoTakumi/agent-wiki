"""Integration tests for the mounted `awiki skills` command group.

The group is AgentSquire's ready-made `skills_command_group`, mounted in
`agent_wiki.cli`. These tests exercise it through the real `awiki` CLI (click
CliRunner) against injected fixture harness trees, so they cover the wiring
(package name, resource path, default scope) rather than re-testing AgentSquire.

Fixture injection: the mounted group resolves its roots via AgentSquire's
AGENTSQUIRE_HOME / AGENTSQUIRE_PROJECT env overrides (agentsquire 0.2.1), so we
point both at fixture trees - no `Path.home` monkeypatch or `chdir` needed. Each
tree carries a `.claude` marker so the claude-code harness is detected in both
scopes; that lets a no-flag install (user scope) and `--scope project` land in
distinct, assertable directories.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_wiki.cli import cli

SKILL_NAMES = ("awiki-search", "awiki-save", "awiki-ingest")


@pytest.fixture
def harness_env(tmp_path, monkeypatch):
    """A fixture home + project, each carrying a claude-code (.claude) marker.

    Returns (home, project). Both marker dirs exist so detection succeeds for
    either scope; the scope flag alone decides where skills land."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    # The mounted skills group's targets() resolves roots via these env overrides
    # (agentsquire 0.2.1), so no Path.home monkeypatch or chdir is needed.
    monkeypatch.setenv("AGENTSQUIRE_HOME", str(home))
    monkeypatch.setenv("AGENTSQUIRE_PROJECT", str(project))
    return home, project


def _claude_skills_dir(root: Path) -> Path:
    return root / ".claude" / "skills"


def test_default_scope_installs_to_user_dir(harness_env):
    """A no-flag `awiki skills install` defaults to user scope: skills land in
    the user home's harness dir and NOT in the project's."""
    home, project = harness_env
    result = CliRunner().invoke(cli, ["skills", "install"])

    assert result.exit_code == 0, result.output
    for name in SKILL_NAMES:
        assert (_claude_skills_dir(home) / name / "SKILL.md").is_file(), (
            f"{name} not installed under user scope\n{result.output}")
        assert not (_claude_skills_dir(project) / name).exists(), (
            f"{name} leaked into project scope on a default install\n{result.output}")


def test_scope_project_installs_to_project_dir(harness_env):
    """`--scope project` overrides the default: skills land in the project's
    harness dir and NOT in the user home's."""
    home, project = harness_env
    result = CliRunner().invoke(cli, ["skills", "install", "--scope", "project"])

    assert result.exit_code == 0, result.output
    for name in SKILL_NAMES:
        assert (_claude_skills_dir(project) / name / "SKILL.md").is_file(), (
            f"{name} not installed under project scope\n{result.output}")
        assert not (_claude_skills_dir(home) / name).exists(), (
            f"{name} leaked into user scope on a --scope project install\n{result.output}")
