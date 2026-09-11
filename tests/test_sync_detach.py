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


def test_detach_skips_when_lock_is_held(detach_env):
    from agent_wiki.locking import file_lock

    vault = detach_env
    log = run_log_path(vault, "sync")
    log.write_text("earlier run output\n")
    with file_lock(vault, "log", timeout=1):
        result = CliRunner().invoke(cli, ["sync", "--detach"])
        assert result.exit_code == 0, result.output
        assert _wait_for(lambda: "already running" in log.read_text())
    assert not (vault / STATE_FILE).exists()
    assert not list((vault / "sessions").glob("*.md"))
    # A skipped sweep appends its notice; only a sweep that holds the lock
    # resets the log, so the earlier run's output survives.
    assert log.read_text().startswith("earlier run output\n")

    CliRunner().invoke(cli, ["sync", "--detach"])
    assert _wait_for(lambda: "3 new" in log.read_text())
    text = log.read_text()
    assert "earlier run output" not in text
    assert "already running" not in text
    assert text.startswith("awiki sync started ")


def test_blocking_sync_still_times_out_when_lock_is_held(detach_env, monkeypatch):
    import contextlib
    from agent_wiki import locking, service

    vault = detach_env

    @contextlib.contextmanager
    def short_timeout(vault_path, name, timeout=locking.DEFAULT_TIMEOUT):
        with locking.file_lock(vault_path, name, timeout=min(timeout, 0.3)):
            yield

    monkeypatch.setattr(service, "file_lock", short_timeout)
    with locking.file_lock(vault, "log", timeout=1):
        result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code != 0
    assert isinstance(result.exception, TimeoutError)
    assert not (vault / STATE_FILE).exists()


@pytest.fixture
def two_vault_env(tmp_path, monkeypatch):
    """Two configured vaults, each with its own pending session; 'work' is default."""
    cc_work = tmp_path / "cc-work"
    _write_cc_session(cc_work, "w1")
    cc_other = tmp_path / "cc-other"
    _write_cc_session(cc_other, "o1")
    work = tmp_path / "work-vault"
    other = tmp_path / "other-vault"
    _make_vault(work, cc_work)
    _make_vault(other, cc_other)

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({
        "default_vault": "work",
        "vaults": {"work": {"path": str(work)}, "other": {"path": str(other)}},
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("AWIKI_VAULT", raising=False)
    return work, other


def test_detach_syncs_default_vault_only(two_vault_env):
    work, other = two_vault_env
    result = CliRunner().invoke(cli, ["sync", "--detach"])
    assert result.exit_code == 0, result.output
    assert _wait_for(lambda: "claude-code:w1" in load_state(work))
    assert _wait_for(lambda: "1 new" in run_log_path(work, "sync").read_text())
    time.sleep(0.5)
    assert not (other / STATE_FILE).exists()
    assert not run_log_path(other, "sync").exists()


def test_detach_with_vault_flag_syncs_only_that_vault(two_vault_env):
    work, other = two_vault_env
    result = CliRunner().invoke(cli, ["--vault", "other", "sync", "--detach"])
    assert result.exit_code == 0, result.output
    assert _wait_for(lambda: "claude-code:o1" in load_state(other))
    assert _wait_for(lambda: "1 new" in run_log_path(other, "sync").read_text())
    time.sleep(0.5)
    assert not (work / STATE_FILE).exists()
    assert not run_log_path(work, "sync").exists()


def test_detach_on_a_remote_default_vault_is_a_clear_noop(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({
        "vaults": {"main": {"url": "http://127.0.0.1:9", "token": "tok"}},
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("AWIKI_VAULT", raising=False)

    result = CliRunner().invoke(cli, ["sync", "--detach"])
    assert result.exit_code == 0, result.output
    assert "remote" in result.output
    assert not (tmp_path / "state").exists()


def test_detach_on_a_legacy_url_plus_path_config_treats_it_as_remote(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _make_vault(vault, tmp_path / "cc")
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({
        "vault_path": str(vault),
        "server": {"url": "http://127.0.0.1:9", "token": "tok"},
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("AWIKI_VAULT", raising=False)

    result = CliRunner().invoke(cli, ["sync", "--detach"])
    assert result.exit_code == 0, result.output
    assert "remote" in result.output
    assert not (tmp_path / "state").exists()
