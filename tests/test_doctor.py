from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.doctor import (
    MissingConversationsBlock,
    MissingDropZoneDir,
    MissingRawSessionsDir,
    MissingSessionsTopic,
    MissingSourcesBlock,
    MissingSummarizerBlock,
    MissingTopicDirs,
    SourcePathMissing,
    run_checks,
)


def _legacy_vault(tmp_path: Path) -> Path:
    """Build a pre-conversation-ingest-era vault layout."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "wiki.yaml").write_text(yaml.dump({
        "vault": {"name": "legacy", "version": 1},
        "topics": ["projects", "decisions", "research", "tools"],
        "default_topic": "research",
    }))
    (vault / "raw").mkdir()
    for t in ("projects", "decisions", "research", "tools"):
        (vault / t).mkdir()
    (vault / "log.md").write_text("# Activity Log\n\n")
    (vault / "index.md").write_text("# Index\n")
    return vault


def test_run_checks_on_legacy_vault_finds_all_expected(tmp_path):
    vault = _legacy_vault(tmp_path)
    findings = run_checks(vault)
    names = [f.check.name for f in findings]
    assert "sessions-topic" in names
    assert "conversations-block" in names
    assert "summarizer-block" in names
    assert "sources-block" in names
    assert "raw-sessions-dir" in names
    # After sources-block is missing, drop-zone-dir check is gated on it — not yet found
    # (Will be found after sources block is added. That's fine.)


def test_fix_adds_sessions_topic(tmp_path):
    vault = _legacy_vault(tmp_path)
    MissingSessionsTopic().fix(vault)
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert "sessions" in config["topics"]


def test_fix_adds_conversations_block(tmp_path):
    vault = _legacy_vault(tmp_path)
    MissingConversationsBlock().fix(vault)
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert config["conversations"]["topic"] == "sessions"


def test_fix_adds_summarizer_block(tmp_path):
    vault = _legacy_vault(tmp_path)
    MissingSummarizerBlock().fix(vault)
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert config["summarizer"]["type"] == "none"


def test_fix_adds_sources_block(tmp_path):
    vault = _legacy_vault(tmp_path)
    MissingSourcesBlock().fix(vault)
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert config["sources"]["claude_code"]["enabled"] is True
    assert config["sources"]["opencode"]["enabled"] is True
    assert config["sources"]["drop_zone"]["enabled"] is True


def test_fix_creates_topic_dirs(tmp_path):
    vault = _legacy_vault(tmp_path)
    # Add a topic but don't create the dir
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    config["topics"].append("sessions")
    (vault / "wiki.yaml").write_text(yaml.dump(config))

    assert not (vault / "sessions").exists()
    MissingTopicDirs().fix(vault)
    assert (vault / "sessions").is_dir()


def test_fix_creates_raw_sessions(tmp_path):
    vault = _legacy_vault(tmp_path)
    MissingRawSessionsDir().fix(vault)
    assert (vault / "raw" / "sessions").is_dir()


def test_drop_zone_check_requires_sources_block(tmp_path):
    vault = _legacy_vault(tmp_path)
    # Without sources block, drop-zone check should not fire
    finding = MissingDropZoneDir().detect(vault)
    assert finding is None

    # Add sources block; now drop-zone dir is missing
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    config["sources"] = {"drop_zone": {"enabled": True, "path": "incoming"}}
    (vault / "wiki.yaml").write_text(yaml.dump(config))

    finding = MissingDropZoneDir().detect(vault)
    assert finding is not None
    MissingDropZoneDir().fix(vault)
    assert (vault / "incoming").is_dir()


def test_source_path_missing_is_informational(tmp_path):
    vault = _legacy_vault(tmp_path)
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    config["sources"] = {
        "claude_code": {"enabled": True, "path": "/nonexistent/xyz"},
    }
    (vault / "wiki.yaml").write_text(yaml.dump(config))

    f = SourcePathMissing().detect(vault)
    assert f is not None
    # Fix is a no-op
    result = SourcePathMissing().fix(vault)
    assert "informational" in result


def test_idempotent_second_run_is_clean(tmp_path):
    vault = _legacy_vault(tmp_path)
    # Apply every fix once
    for check in [MissingSessionsTopic(), MissingConversationsBlock(),
                  MissingSummarizerBlock(), MissingSourcesBlock(),
                  MissingTopicDirs(), MissingRawSessionsDir()]:
        check.fix(vault)
    # drop-zone dir needs sources to exist, so run it after
    MissingDropZoneDir().fix(vault)

    findings = run_checks(vault)
    # Only the informational source-path-missing might fire (depends on whether
    # ~/.claude/projects exists on the test machine). Filter that out.
    non_info = [f for f in findings if not isinstance(f.check, SourcePathMissing)]
    assert non_info == [], f"unexpected findings: {[f.detail for f in non_info]}"


def test_cli_doctor_dry_run(tmp_path, monkeypatch):
    vault = _legacy_vault(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "sessions-topic" in result.output
    assert "sources-block" in result.output
    assert "0 applied" in result.output

    # Vault should be unchanged
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert "sessions" not in config.get("topics", [])


def test_cli_doctor_fix_all(tmp_path, monkeypatch):
    vault = _legacy_vault(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--fix"])
    assert result.exit_code == 0, result.output

    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert "sessions" in config["topics"]
    assert config["conversations"]["topic"] == "sessions"
    assert config["summarizer"]["type"] == "none"
    assert "claude_code" in config["sources"]
    assert (vault / "raw" / "sessions").is_dir()


def test_cli_doctor_interactive_accept(tmp_path, monkeypatch):
    vault = _legacy_vault(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))

    runner = CliRunner()
    # Feed newlines — default is yes for every prompt
    result = runner.invoke(cli, ["doctor"], input="\n" * 20)
    assert result.exit_code == 0, result.output

    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    assert "sessions" in config["topics"]


def test_cli_doctor_clean_vault(tmp_path, monkeypatch):
    # Freshly-initialized vaults should be clean out of the box.
    from agent_wiki.vault import init_vault
    cfg_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cfg_dir))
    init_vault(tmp_path / "fresh")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 0, result.output
    # Either nothing found at all, or only the informational source-path check.
    assert (
        "No issues found." in result.output
        or "informational" in result.output
        or "0 applied" in result.output
    )


def test_raw_content_drift_detect(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file
    from agent_wiki.doctor import RawContentDrift

    src = tmp_path / "d.md"
    src.write_text("# D\n\noriginal\n")
    page = ingest_file(src, tmp_vault, topic="research")

    # No drift right after ingest.
    assert RawContentDrift().detect(tmp_vault) is None

    # Hand-edit the page body -> drift.
    page.write_text(page.read_text().replace("original", "edited by hand"))
    finding = RawContentDrift().detect(tmp_vault)
    assert finding is not None
    assert "d.md" in finding.detail


def test_raw_content_drift_skips_binary_raw(tmp_vault):
    from agent_wiki.doctor import RawContentDrift
    # A page whose source points at a non-UTF-8 raw file must be skipped, not crash.
    (tmp_vault / "raw" / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    (tmp_vault / "research" / "blob.md").write_text(
        "---\ntitle: Blob\ntopic: research\nsources:\n- raw/blob.bin\n---\n\nbody\n"
    )
    # Does not raise; the undecodable raw source is simply not reported as drift.
    assert RawContentDrift().detect(tmp_vault) is None


def _drift_vault(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "d.md"
    src.write_text("# D\n\noriginal\n")
    page = ingest_file(src, tmp_vault, topic="research")
    page.write_text(page.read_text().replace("original", "edited by hand"))
    return tmp_vault


def test_blanket_fix_does_not_rewrite_raw(tmp_vault, tmp_path):
    from agent_wiki.service import LocalVaultService
    vault = _drift_vault(tmp_vault, tmp_path)
    LocalVaultService(vault).doctor(fix=True)            # schema fixes only
    assert (vault / "raw" / "d.md").read_text() == "# D\n\noriginal\n"


def test_reconcile_raw_rewrites_from_page(tmp_vault, tmp_path):
    from agent_wiki.service import LocalVaultService
    vault = _drift_vault(tmp_vault, tmp_path)
    LocalVaultService(vault).doctor(reconcile_raw=True)
    assert "edited by hand" in (vault / "raw" / "d.md").read_text()
