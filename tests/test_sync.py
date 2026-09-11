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
         "cwd": "/home/user/AI/Projects/agent-wiki",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "sessionId": session_id, "timestamp": "2026-04-18T10:00:05Z",
         "cwd": "/home/user/AI/Projects/agent-wiki",
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
            "cwd": "/home/user/AI/Projects/agent-wiki",
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


# --- multi-vault: sync touches exactly one vault -------------------------------

def test_sync_touches_only_default_vault(tmp_path, monkeypatch):
    from conftest import make_vault

    work = tmp_path / "work-vault"
    work.mkdir()
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1", title="Multi Vault Sync")
    config = {
        "vault": {"name": "w", "version": 1},
        "topics": ["research", "sessions"],
        "default_topic": "research",
        "conversations": {"topic": "sessions"},
        "summarizer": {"type": "none"},
        "sources": {
            "claude_code": {"enabled": True, "path": str(cc_root),
                            "include_live": True},
            "opencode": {"enabled": False},
            "drop_zone": {"enabled": False},
        },
    }
    (work / "wiki.yaml").write_text(yaml.dump(config))
    (work / "raw").mkdir()
    (work / "raw" / "sessions").mkdir()
    for t in config["topics"]:
        (work / t).mkdir()
    (work / "log.md").write_text("# Activity Log\n\n")

    personal = make_vault(tmp_path / "personal-vault")
    personal_before = sorted(str(p) for p in personal.rglob("*"))

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({
        "default_vault": "work",
        "vaults": {"work": {"path": str(work)},
                   "personal": {"path": str(personal)}},
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))

    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert "claude-code:s1" in result.output

    # The sync landed in the default vault; the other vault is untouched.
    assert list((work / "sessions").glob("*.md"))
    assert sorted(str(p) for p in personal.rglob("*")) == personal_before


class _CountingAdapter:
    """Test double: file-backed sessions with a to_bundle call counter."""

    name = "claude-code"

    def __init__(self, config=None):
        self.config = config or {}
        self.root = Path(self.config["path"])
        self.since = None
        self.to_bundle_calls = 0
        _COUNTING_INSTANCES.append(self)

    def discover(self):
        return sorted(self.root.rglob("*.jsonl"))

    def session_key(self, ref):
        return f"{self.name}:{ref.stem}"

    def fingerprint(self, ref):
        st = ref.stat()
        return f"mtime:{int(st.st_mtime)}:size:{st.st_size}"

    def to_bundle(self, ref):
        from agent_wiki.adapters.claude_code import convert_jsonl
        self.to_bundle_calls += 1
        return convert_jsonl(ref)


_COUNTING_INSTANCES: list = []


@pytest.fixture
def counting_adapter(monkeypatch):
    _COUNTING_INSTANCES.clear()
    import agent_wiki.sync as sync_mod
    monkeypatch.setattr(sync_mod, "build_adapter", lambda name, cfg: _CountingAdapter(cfg))
    return _COUNTING_INSTANCES


def test_sync_rerun_skips_without_parsing(tmp_vault, tmp_path, counting_adapter):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1")
    _write_cc_session(cc_root, "s2")
    _configure_vault_with_cc(tmp_vault, cc_root)

    first = sync(tmp_vault)
    assert [r.action for r in first] == ["new", "new"]
    assert counting_adapter[-1].to_bundle_calls == 2

    second = sync(tmp_vault)
    assert [r.action for r in second] == ["skipped", "skipped"]
    assert [r.key for r in second] == ["claude-code:s1", "claude-code:s2"]
    assert counting_adapter[-1].to_bundle_calls == 0


def test_sync_honours_preexisting_state_without_parsing(tmp_vault, tmp_path, counting_adapter):
    cc_root = tmp_path / "claude-projects"
    jsonl = _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)

    st = jsonl.stat()
    (tmp_vault / STATE_FILE).write_text(json.dumps({
        "claude-code:s1": {
            "fingerprint": f"mtime:{int(st.st_mtime)}:size:{st.st_size}",
            "bundle": "raw/sessions/claude-code-s1.md",
            "page": "sessions/claude-code-s1.md",
            "last_sync": "2026-04-18T12:00:00",
        }
    }))

    results = sync(tmp_vault)
    assert [r.action for r in results] == ["skipped"]
    assert counting_adapter[-1].to_bundle_calls == 0
    assert load_state(tmp_vault)["claude-code:s1"]["last_sync"] == "2026-04-18T12:00:00"


def test_sync_changed_fingerprint_reingests_as_updated(tmp_vault, tmp_path, counting_adapter):
    cc_root = tmp_path / "claude-projects"
    jsonl = _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)

    sync(tmp_vault)
    t = time.time() + 5
    os.utime(jsonl, (t, t))

    results = sync(tmp_vault)
    assert [r.action for r in results] == ["updated"]
    assert counting_adapter[-1].to_bundle_calls == 1


# --- pi as a first-class source ------------------------------------------------

_PI_SESSION_ID = "019fd2a9-7e14-787f-a8e2-178a5399e40e"


def _write_pi_session(root: Path, session_id: str = _PI_SESSION_ID) -> Path:
    path = root / "--home-user-AI-Projects-herdr--" / f"2026-08-05T16-02-31-060Z_{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session", "version": 3, "id": session_id,
         "timestamp": "2026-08-05T16:02:31.060Z", "cwd": "/home/user/AI/Projects/herdr"},
        {"type": "message", "id": "e1", "parentId": None, "timestamp": "2026-08-05T16:04:46.957Z",
         "message": {"role": "user", "content": "Hello pi", "timestamp": 1}},
        {"type": "message", "id": "e2", "parentId": "e1", "timestamp": "2026-08-05T16:05:04.352Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}],
                     "model": "m", "usage": {"input": 1, "output": 1}, "stopReason": "stop",
                     "timestamp": 1}},
    ]
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def _configure_vault_with_pi(tmp_vault, pi_root: Path) -> None:
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    if "sessions" not in config["topics"]:
        config["topics"].append("sessions")
    config["conversations"] = {"topic": "sessions"}
    config["sources"] = {
        "claude_code": {"enabled": False},
        "opencode": {"enabled": False},
        "pi": {"enabled": True, "path": str(pi_root), "include_live": True},
        "drop_zone": {"enabled": False},
    }
    config["summarizer"] = {"type": "none"}
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))
    (tmp_vault / "sessions").mkdir(exist_ok=True)


def test_sync_source_pi_ingests_fixture_dir(tmp_config, tmp_vault, tmp_path):
    pi_root = tmp_path / "pi-sessions"
    _write_pi_session(pi_root)
    _configure_vault_with_pi(tmp_vault, pi_root)

    dry = CliRunner().invoke(cli, ["sync", "--source", "pi", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert f"pi:{_PI_SESSION_ID}" in dry.output

    result = CliRunner().invoke(cli, ["sync", "--source", "pi"])
    assert result.exit_code == 0, result.output
    assert (tmp_vault / "raw" / "sessions" / f"pi-{_PI_SESSION_ID}.md").exists()
    assert (tmp_vault / "sessions" / f"pi-{_PI_SESSION_ID}.md").exists()
    assert f"pi:{_PI_SESSION_ID}" in load_state(tmp_vault)


def test_adapt_pi_accepts_a_session_file(tmp_config, tmp_vault, tmp_path):
    pi_root = tmp_path / "pi-sessions"
    jsonl = _write_pi_session(pi_root)
    _configure_vault_with_pi(tmp_vault, pi_root)
    out = tmp_path / "bundle.md"

    result = CliRunner().invoke(cli, ["adapt", "pi", str(jsonl), "-o", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text()
    assert "agent: pi" in text
    assert f"session_id: {_PI_SESSION_ID}" in text


class _MismatchedKeyAdapter(_CountingAdapter):
    """Cheap key disagrees with the parsed session id (e.g. a transcript whose
    filename is not its session id): sync must still skip unchanged sessions."""

    def session_key(self, ref):
        return f"{self.name}:file-{ref.stem}"


@pytest.fixture
def mismatched_adapter(monkeypatch):
    _COUNTING_INSTANCES.clear()
    import agent_wiki.sync as sync_mod
    monkeypatch.setattr(sync_mod, "build_adapter", lambda name, cfg: _MismatchedKeyAdapter(cfg))
    return _COUNTING_INSTANCES


def test_sync_rerun_skips_when_cheap_key_disagrees_with_parsed_id(tmp_vault, tmp_path, mismatched_adapter):
    cc_root = tmp_path / "claude-projects"
    _write_cc_session(cc_root, "s1")
    _configure_vault_with_cc(tmp_vault, cc_root)

    first = sync(tmp_vault)
    assert [r.action for r in first] == ["new"]
    assert list(load_state(tmp_vault)) == ["claude-code:s1"]

    second = sync(tmp_vault)
    assert [r.action for r in second] == ["skipped"]
    assert mismatched_adapter[-1].to_bundle_calls == 1
    assert list(load_state(tmp_vault)) == ["claude-code:s1"]

    dry = sync(tmp_vault, dry_run=True)
    assert [r.action for r in dry] == ["skipped"]


def test_sync_since_accepts_a_bare_date(tmp_config, tmp_vault, tmp_path):
    pi_root = tmp_path / "pi-sessions"
    _write_pi_session(pi_root)
    _configure_vault_with_pi(tmp_vault, pi_root)

    result = CliRunner().invoke(cli, ["sync", "--source", "pi", "--dry-run", "--since", "2026-01-01"])
    assert result.exit_code == 0, result.output
    assert "1 new" in result.output

    result = CliRunner().invoke(cli, ["sync", "--source", "pi", "--dry-run", "--since", "2999-01-01"])
    assert result.exit_code == 0, result.output
    assert "0 new" in result.output


def test_sync_include_live_reaches_the_adapters(tmp_config, tmp_vault, tmp_path):
    pi_root = tmp_path / "pi-sessions"
    _write_pi_session(pi_root)
    _configure_vault_with_pi(tmp_vault, pi_root)
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    config["sources"]["pi"]["include_live"] = False
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))

    quiet = CliRunner().invoke(cli, ["sync", "--source", "pi", "--dry-run"])
    assert quiet.exit_code == 0, quiet.output
    assert "0 new" in quiet.output

    live = CliRunner().invoke(cli, ["sync", "--source", "pi", "--dry-run", "--include-live"])
    assert live.exit_code == 0, live.output
    assert "1 new" in live.output
