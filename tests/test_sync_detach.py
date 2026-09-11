"""``awiki sync --detach``: fork, return at once, log to the state dir."""
import json
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.locking import run_log_path
from agent_wiki.sync import STATE_FILE, load_state


def _write_cc_session(root: Path, session_id: str) -> Path:
    path = root / "proj" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "sessionId": session_id, "timestamp": "2026-04-18T10:00:00Z",
         "cwd": "/home/user/AI/Projects/agent-wiki",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "sessionId": session_id, "timestamp": "2026-04-18T10:00:05Z",
         "cwd": "/home/user/AI/Projects/agent-wiki",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "hi"}],
                     "model": "claude-opus-4-7"}},
    ]
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def _make_vault(vault: Path, cc_root: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    config = {
        "vault": {"name": vault.name, "version": 1},
        "topics": ["research", "sessions"],
        "default_topic": "research",
        "conversations": {"topic": "sessions"},
        "summarizer": {"type": "none"},
        "sources": {
            "claude_code": {"enabled": True, "path": str(cc_root), "include_live": True},
            "opencode": {"enabled": False},
            "drop_zone": {"enabled": False},
        },
    }
    (vault / "wiki.yaml").write_text(yaml.dump(config))
    (vault / "raw" / "sessions").mkdir(parents=True)
    for t in config["topics"]:
        (vault / t).mkdir()
    (vault / "log.md").write_text("# Activity Log\n\n")


@pytest.fixture
def detach_env(tmp_path, monkeypatch):
    """A vault with three pending sessions, isolated config and state dirs."""
    cc_root = tmp_path / "claude-projects"
    for sid in ("s1", "s2", "s3"):
        _write_cc_session(cc_root, sid)
    vault = tmp_path / "vault"
    _make_vault(vault, cc_root)

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    return vault


def _wait_for(predicate, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def test_detach_returns_fast_and_child_syncs(detach_env, tmp_path):
    vault = detach_env
    started = time.monotonic()
    result = CliRunner().invoke(cli, ["sync", "--detach"])
    elapsed = time.monotonic() - started
    assert result.exit_code == 0, result.output
    assert elapsed < 2.0

    def _synced():
        return {"claude-code:s1", "claude-code:s2", "claude-code:s3"} <= set(load_state(vault))

    assert _wait_for(_synced), "child sync did not land in the state file"

    log = run_log_path(vault, "sync")
    assert log.parent.is_relative_to(tmp_path / "state")
    assert _wait_for(lambda: "3 new" in log.read_text())


def test_detach_log_is_overwritten_each_run(detach_env):
    vault = detach_env
    CliRunner().invoke(cli, ["sync", "--detach"])
    log = run_log_path(vault, "sync")
    assert _wait_for(lambda: log.exists() and "3 new" in log.read_text())
    assert _wait_for(lambda: len(load_state(vault)) == 3)

    result = CliRunner().invoke(cli, ["sync", "--detach"])
    assert result.exit_code == 0, result.output
    assert _wait_for(lambda: "0 new" in log.read_text())
    text = log.read_text()
    assert "3 new" not in text
    assert "3 unchanged" in text
