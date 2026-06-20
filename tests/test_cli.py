from click.testing import CliRunner
from agent_wiki.cli import cli
from agent_wiki.page import render_page
import yaml


def _setup_vault(tmp_path, monkeypatch):
    """Set up a vault and config for CLI testing."""
    vault = tmp_path / "vault"
    vault.mkdir()
    config = {
        "vault": {"name": "Test Wiki", "version": 1},
        "topics": ["projects", "decisions", "research", "tools"],
        "default_topic": "research",
    }
    (vault / "wiki.yaml").write_text(yaml.dump(config))
    (vault / "raw").mkdir()
    (vault / "index.md").write_text("# Index\n")
    (vault / "log.md").write_text("# Activity Log\n\n- **2026-04-14 10:00** — ingest: test.md -> research/test.md\n")
    for topic in config["topics"]:
        (vault / topic).mkdir()

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({"vault_path": str(vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return vault


def test_status_command(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)

    meta = {
        "title": "Test Page", "topic": "research", "tags": [],
        "created": "2026-04-14", "updated": "2026-04-14", "sources": [],
    }
    (vault / "research" / "test.md").write_text(
        render_page(meta, "# Test Page\n\nContent.\n")
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "research" in result.output
    assert "1" in result.output


def test_log_command(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["log"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "test.md" in result.output


def test_log_command_last(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["log", "--last", "1"])
    assert result.exit_code == 0
    assert "test.md" in result.output


def _make_page(vault, slug, title, body):
    meta = {
        "title": title, "topic": "research", "tags": [],
        "created": "2026-05-30", "updated": "2026-05-30", "sources": [],
    }
    (vault / "research" / f"{slug}.md").write_text(render_page(meta, body))


def test_search_shows_partial_tier_with_coverage(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    _make_page(vault, "full", "Full Match", "# Full Match\n\nalpha beta gamma\n")
    _make_page(vault, "part", "Partial Match", "# Partial Match\n\nalpha only\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "alpha beta gamma"])
    assert result.exit_code == 0
    assert "Full Match" in result.output
    assert "Partial matches" in result.output
    assert "Partial Match" in result.output
    assert "(1/3 terms)" in result.output
    # Nothing was truncated (1 all + 1 partial, well under the caps).
    assert "Showing" not in result.output
    # All-terms tier prints before the partial section.
    assert result.output.index("Full Match") < result.output.index("Partial matches")


def test_search_caps_all_tier_and_reports_truncation(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    for i in range(5):
        _make_page(vault, f"p{i}", f"Page {i}", f"# Page {i}\n\nalpha beta\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "alpha beta", "--limit", "2"])
    assert result.exit_code == 0
    assert "Showing 2 of 5 matches" in result.output
    assert "narrow your query" in result.output


def test_search_no_results(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "nonexistentterm"])
    assert result.exit_code == 0
    assert "No results found." in result.output


def test_search_partial_only_has_no_leading_blank(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    _make_page(vault, "a", "Only Alpha", "# Only Alpha\n\nalpha here\n")
    _make_page(vault, "b", "Only Beta", "# Only Beta\n\nbeta here\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "alpha beta"])
    assert result.exit_code == 0
    # No page has BOTH terms → all-tier empty → partial header is the first line.
    assert result.output.startswith("Partial matches")


def test_search_caps_partial_tier_and_reports_truncation(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    # 7 pages each containing only ONE of two query terms → all partial matches.
    for i in range(7):
        _make_page(vault, f"q{i}", f"Partial {i}", f"# Partial {i}\n\nalpha only\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "alpha beta"])
    assert result.exit_code == 0
    assert "Partial matches" in result.output
    assert result.output.count("(1/2 terms)") == 5   # partial tier capped at 5
    assert "Showing 5 of 7 matches" in result.output


def test_show_command_prints_page_verbatim(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    meta = {
        "title": "Raft Consensus", "topic": "research", "tags": ["consensus"],
        "created": "2026-04-14", "updated": "2026-04-14", "sources": [],
    }
    page = render_page(meta, "# Raft Consensus\n\nRaft is a consensus algorithm.\n")
    (vault / "research" / "raft.md").write_text(page)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "research/raft.md"])
    assert result.exit_code == 0
    # Verbatim: output is byte-for-byte the file (frontmatter + body).
    assert result.output == page


def test_show_command_rejects_traversal_without_leaking(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET-DO-NOT-LEAK\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "../secret.txt"])
    assert result.exit_code == 1
    assert "outside the vault" in result.output
    assert "TOPSECRET" not in result.output


def test_show_command_missing_page_errors(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["show", "research/nope.md"])
    assert result.exit_code == 1
    assert "no such page" in result.output


def test_show_command_directory_errors(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["show", "research"])
    assert result.exit_code == 1
    assert "no such page" in result.output


def test_show_command_binary_file_errors(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    (vault / "raw" / "blob.bin").write_bytes(b"\xff\xfe\x00\x01\x80")
    runner = CliRunner()
    result = runner.invoke(cli, ["show", "raw/blob.bin"])
    assert result.exit_code == 1
    assert "cannot display binary file" in result.output


def test_cli_ingest_collision_skips_and_exits(tmp_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    runner = CliRunner()
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    assert runner.invoke(cli, ["ingest", str(src), "-t", "research"]).exit_code == 0

    clash = tmp_path / "sub" / "notes.md"
    clash.parent.mkdir()
    clash.write_text("# Clash\n\nx\n")
    res = runner.invoke(cli, ["ingest", str(clash), "-t", "research"])
    assert res.exit_code == 1
    assert "already exists" in res.output


def test_cli_ingest_update_succeeds(tmp_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    runner = CliRunner()
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    runner.invoke(cli, ["ingest", str(src), "-t", "research"])
    src.write_text("# Notes\n\nv2\n")
    res = runner.invoke(cli, ["ingest", str(src), "-t", "research", "--update"])
    assert res.exit_code == 0
    assert "Updated" in res.output


def test_cli_ingest_update_ambiguous_skips_not_crash(tmp_config, tmp_vault, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    # one raw file linked by TWO pages -> ambiguous update -> ValueError in ingest_file
    (tmp_vault / "raw" / "dup.md").write_text("x\n")
    for name in ("one", "two"):
        (tmp_vault / "research" / f"{name}.md").write_text(
            "---\ntitle: " + name + "\ntopic: research\nsources:\n- raw/dup.md\n---\n\nbody\n"
        )
    src = tmp_path / "dup.md"
    src.write_text("# Dup\n\nnew\n")
    res = CliRunner().invoke(cli, ["ingest", str(src), "-t", "research", "--update"])
    assert res.exit_code == 1
    assert "skipped" in res.output
    # handled cleanly as a skip, not an uncaught crash
    assert res.exception is None or isinstance(res.exception, SystemExit)


def test_cli_doctor_reconcile_raw_local(tmp_config, tmp_vault, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "d.md"
    src.write_text("# D\n\noriginal\n")
    page = ingest_file(src, tmp_vault, topic="research")
    page.write_text(page.read_text().replace("original", "edited by hand"))

    runner = CliRunner()
    res = runner.invoke(cli, ["doctor", "--reconcile-raw", "--fix"])
    assert res.exit_code == 0
    assert "edited by hand" in (tmp_vault / "raw" / "d.md").read_text()


def test_cli_doctor_reconcile_raw_remote_refused(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    from agent_wiki.remote import RemoteVaultService

    class _Fake(RemoteVaultService):
        def __init__(self):
            pass
    monkeypatch.setattr("agent_wiki.cli._service", lambda: _Fake())

    res = CliRunner().invoke(cli, ["doctor", "--reconcile-raw"])
    assert res.exit_code != 0
    assert "must be run on the server" in res.output


def test_ingest_update_same_raw_path_no_crash(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n\nv1\n")
    assert runner.invoke(cli, ["ingest", str(src), "--topic", "research"]).exit_code == 0
    raw = vault / "raw" / "doc.md"             # the vault's OWN raw path
    result = runner.invoke(cli, ["ingest", "--update", str(raw)])
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_ingest_update_refuses_diverged_then_force(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n\nv1\n")
    runner.invoke(cli, ["ingest", str(src), "--topic", "research"])
    page = vault / "research" / "doc.md"
    page.write_text(page.read_text().replace("v1", "v1\n\nhand edit"))
    src.write_text("# Doc\n\nv2\n")

    refused = runner.invoke(cli, ["ingest", "--update", str(src)])
    assert refused.exit_code == 1
    assert "differs from" in refused.output       # message
    assert "hand edit" in refused.output          # inline diff (a '-' line)
    assert "hand edit" in page.read_text()        # not overwritten

    forced = runner.invoke(cli, ["ingest", "--update", "--force", str(src)])
    assert forced.exit_code == 0
    assert "v2" in page.read_text()
    assert "hand edit" not in page.read_text()
