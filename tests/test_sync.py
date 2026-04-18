import json
import os
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.sync import STATE_FILE, load_state, sync


def _configure_vault_with_cc(tmp_vault, cc_root: Path) -> None:
    """Point the tmp_vault wiki.yaml at a throwaway Claude Code root."""
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    if "sessions" not in config["topics"]:
        config["topics"].append("sessions")
    config["conversations"] = {"topic": "sessions", "include_live": False}
    config["sources"] = {
        "claude_code": {"enabled": True, "path": str(cc_root), "include_live": True},
        "opencode": {"enabled": False},
        "drop_zone": {"enabled": False},
    }
    config["summarizer"] = {"type": "none"}
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))
    (tmp_vault / "sessions").mkdir(exist_ok=True)


def _write_cc_session(root: Path, session_id: str, title: str = "Hello") -> Path:
    path = root / "proj" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "sessionId": session_id, "timestamp": "2026-04-18T10:00:00Z",
         "cwd": "/home/rob/AI/Projects/agent-wiki",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "sessionId": session_id, "timestamp": "2026-04-18T10:00:05Z",
         "cwd": "/home/rob/AI/Projects/agent-wiki",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "hi"}],
                     "model": "claude-opus-4-7",
                     "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"type": "custom-title", "customTitle": title, "sessionId": session_id},
    ]
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def test_sync_creates_bundle_page_and_state(tmp_vault, tmp_path):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1", title="First session")
    _configure_vault_with_cc(tmp_vault, cc_root)

    results = sync(tmp_vault)
    assert [r.action for r in results] == ["new"]
    assert results[0].bundle is not None
    assert results[0].page is not None
    assert results[0].page.parent.name == "sessions"

    # Bundle written under raw/sessions/
    bundles = list((tmp_vault / "raw" / "sessions").glob("*.md"))
    assert len(bundles) == 1
    assert bundles[0].name == "claude-code-s1.md"

    # State persisted
    state = load_state(tmp_vault)
    assert "claude-code:s1" in state
    entry = state["claude-code:s1"]
    assert entry["bundle"].startswith("raw/sessions/")
    assert entry["page"].startswith("sessions/")


def test_sync_is_idempotent(tmp_vault, tmp_path):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)

    sync(tmp_vault)
    results = sync(tmp_vault)
    assert [r.action for r in results] == ["skipped"]


def test_sync_detects_changed_session(tmp_vault, tmp_path):
    cc_root = tmp_path / "claude-projects"
    jsonl = _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)

    sync(tmp_vault)

    # Append a new turn so mtime + size change
    with open(jsonl, "a") as f:
        f.write(json.dumps({
            "type": "user", "sessionId": "s1",
            "timestamp": "2026-04-18T11:00:00Z",
            "cwd": "/home/rob/AI/Projects/agent-wiki",
            "message": {"role": "user", "content": "more"},
        }) + "\n")
    t = time.time() + 5
    os.utime(jsonl, (t, t))

    results = sync(tmp_vault)
    assert [r.action for r in results] == ["updated"]


def test_sync_rebuilds_index(tmp_vault, tmp_path):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1", title="Indexed session")
    _configure_vault_with_cc(tmp_vault, cc_root)

    # Index starts stale
    (tmp_vault / "index.md").write_text("# Index\n\n(stale)\n")
    sync(tmp_vault)

    index = (tmp_vault / "index.md").read_text()
    assert "(stale)" not in index
    assert "Indexed session" in index


def test_sync_skips_index_when_nothing_changed(tmp_vault, tmp_path):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)
    sync(tmp_vault)

    # Hand-edit index.md to sentinel; a second sync with no changes should leave it alone
    (tmp_vault / "index.md").write_text("# Index\n\nSENTINEL\n")
    sync(tmp_vault)
    assert "SENTINEL" in (tmp_vault / "index.md").read_text()


def test_sync_dry_run_does_not_write(tmp_vault, tmp_path):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)

    results = sync(tmp_vault, dry_run=True)
    assert [r.action for r in results] == ["new"]
    assert not (tmp_vault / STATE_FILE).exists()
    assert list((tmp_vault / "raw" / "sessions").glob("*.md")) == []
    assert list((tmp_vault / "sessions").glob("*.md")) == []


def test_sync_cli_outputs_summary(tmp_path, monkeypatch):
    # Full CLI path: set up vault + user config, run `awiki sync`.
    vault = tmp_path / "vault"
    vault.mkdir()
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1", title="Via CLI")

    config = {
        "vault": {"name": "t", "version": 1},
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
    (vault / "raw").mkdir()
    (vault / "raw" / "sessions").mkdir()
    for t in config["topics"]:
        (vault / t).mkdir()
    (vault / "log.md").write_text("# Activity Log\n\n")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))

    runner = CliRunner()
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert "[NEW]" in result.output
    assert "claude-code:s1" in result.output
    assert "1 new" in result.output


def test_sync_cli_source_filter(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1")

    config = {
        "vault": {"name": "t", "version": 1},
        "topics": ["research", "sessions"],
        "default_topic": "research",
        "conversations": {"topic": "sessions"},
        "sources": {
            "claude_code": {"enabled": False, "path": str(cc_root), "include_live": True},
        },
    }
    (vault / "wiki.yaml").write_text(yaml.dump(config))
    (vault / "raw").mkdir()
    (vault / "raw" / "sessions").mkdir()
    for t in config["topics"]:
        (vault / t).mkdir()
    (vault / "log.md").write_text("# Activity Log\n\n")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))

    # Source disabled by default → zero results
    runner = CliRunner()
    default = runner.invoke(cli, ["sync"])
    assert default.exit_code == 0
    assert "0 new" in default.output

    # Forcing --source bypasses the enabled check
    forced = runner.invoke(cli, ["sync", "--source", "claude-code"])
    assert forced.exit_code == 0
    assert "1 new" in forced.output
