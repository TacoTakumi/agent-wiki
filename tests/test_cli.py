from pathlib import Path

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
    # Verbatim: stdout is byte-for-byte the file (frontmatter + body). The resolved
    # read location goes to stderr, so stdout stays clean for parsers.
    assert result.stdout == page
    assert str(vault / "research" / "raft.md") in result.stderr


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


def test_reingest_command_raw_edit_succeeds_page_edit_refuses(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n\nv1\n")
    runner.invoke(cli, ["ingest", str(src), "--topic", "research"])

    # Raw-only edit: reingest rebuilds from raw with no --force.
    (vault / "raw" / "doc.md").write_text("# Doc\n\nv2 in raw\n")
    ok = runner.invoke(cli, ["reingest", "doc"])
    assert ok.exit_code == 0
    assert "v2 in raw" in (vault / "research" / "doc.md").read_text()

    # Genuine out-of-band page hand-edit still refuses, with a diff, until --force.
    page = vault / "research" / "doc.md"
    page.write_text(page.read_text().replace("v2 in raw", "v2 in raw\n\nhand edit"))
    refused = runner.invoke(cli, ["reingest", "doc"])
    assert refused.exit_code != 0
    assert "differs from" in refused.output       # page-vs-raw diff (to stderr)
    assert "hand edit" in page.read_text()         # not overwritten

    forced = runner.invoke(cli, ["reingest", "doc", "--force"])
    assert forced.exit_code == 0
    assert "hand edit" not in (vault / "research" / "doc.md").read_text()


def test_reingest_prints_page_location_to_stderr(tmp_path, monkeypatch):
    # reingest surfaces where the page it wrote landed — on stderr for a
    # local vault, the absolute filesystem path — while stdout keeps its existing
    # byte-clean "Reingested" line (skills parse stdout verbatim).
    vault = _setup_vault(tmp_path, monkeypatch)
    runner = CliRunner()
    src = tmp_path / "loc.md"
    src.write_text("# Loc\n\nv1\n")
    runner.invoke(cli, ["ingest", str(src), "--topic", "research"])

    # Raw-only edit so reingest rebuilds from raw with no --force.
    (vault / "raw" / "loc.md").write_text("# Loc\n\nv2 in raw\n")
    result = runner.invoke(cli, ["reingest", "loc"])
    assert result.exit_code == 0, result.output

    page_abs = str(vault / "research" / "loc.md")
    assert page_abs in result.stderr          # location -> stderr
    assert "Reingested" in result.stdout      # existing line stays on stdout
    assert page_abs not in result.stdout      # stdout stays clean of the abs path


# --- awiki raw <name> resolver -----------------------------------------------


def test_raw_resolver_local_prints_absolute_path(tmp_path, monkeypatch):
    # On a local vault, `awiki raw <name>` prints the raw file's absolute
    # path to stdout (usable in $(...) command substitution) and exits 0.
    vault = _setup_vault(tmp_path, monkeypatch)
    (vault / "raw" / "loc.md").write_text("# Loc\n\nbody\n")

    result = CliRunner().invoke(cli, ["raw", "loc"])
    assert result.exit_code == 0, result.output
    printed = result.stdout.strip()
    assert printed == str(vault / "raw" / "loc.md")
    assert printed.endswith("raw/loc.md")


def test_raw_resolver_unknown_name_errors(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli, ["raw", "nope"])
    assert result.exit_code != 0                 # not found -> non-zero
    assert "no raw file matching" in result.output


def test_raw_resolver_ambiguous_stem_errors(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    # Two raw files share the stem 'dup' -> the bare stem is ambiguous.
    (vault / "raw" / "dup.md").write_text("md\n")
    (vault / "raw" / "dup.txt").write_text("txt\n")

    result = CliRunner().invoke(cli, ["raw", "dup"])
    assert result.exit_code != 0                 # ambiguous -> non-zero
    assert "ambiguous" in result.output


def test_raw_resolver_remote_prints_ref_and_note(remote_service, tmp_vault, monkeypatch):
    # On a remote vault the raw source is server-side: print the server ref on
    # stdout and note on stderr that it is not directly editable locally.
    from agent_wiki import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_service", lambda: remote_service)

    result = CliRunner().invoke(cli_mod.cli, ["raw", "loc"])
    assert result.exit_code == 0, result.output
    assert remote_service.base in result.stdout        # server URL
    assert "raw/loc" in result.stdout                  # server-side raw ref
    assert str(tmp_vault) not in result.stdout         # no local absolute path
    assert "not" in result.stderr.lower() and "editable" in result.stderr.lower()


# --- lint type/label mapping -------------------------------------------------

def test_lint_labels_are_distinct():
    # Each lint type maps to its own CLI label -- no two share one, so output
    # never conflates two different checks under the same tag.
    from agent_wiki.cli import LINT_LABELS
    labels = list(LINT_LABELS.values())
    assert len(set(labels)) == len(labels), labels


def test_lint_labels_cover_every_lint_type():
    # Every type lint can emit has an explicit label (none falls back to the
    # generic .upper()); the two manifests stay in lockstep.
    from agent_wiki.cli import LINT_LABELS
    from agent_wiki.lint import LINT_TYPES
    assert set(LINT_TYPES) == set(LINT_LABELS), (
        set(LINT_TYPES).symmetric_difference(LINT_LABELS))


def test_lint_label_renders_in_output(tmp_path, monkeypatch):
    # The mapping is actually used: an over-long page surfaces under [SIZE].
    vault = _setup_vault(tmp_path, monkeypatch)
    body = "\n".join(f"line {i}" for i in range(501)) + "\n"
    meta = {"title": "Big", "topic": "research", "tags": [],
            "created": "2026-04-14", "updated": "2026-04-14", "sources": []}
    (vault / "research" / "big.md").write_text(render_page(meta, body))
    result = CliRunner().invoke(cli, ["lint"])
    assert result.exit_code == 0
    assert "[SIZE]" in result.output


# --- multi-vault dispatch for raw and reingest --------------------------------

def _seed_ingested(vault, name, body):
    """Ingest a real page (raw + rendered) into a vault; returns the raw path."""
    import tempfile, os
    from agent_wiki.ingest import ingest_file
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, name)
        with open(src, "w") as f:
            f.write(body)
        ingest_file(Path(src), vault)
    return vault / "raw" / name


def test_raw_qualified_ref_targets_named_vault(two_vault_config, tmp_path):
    personal = tmp_path / "personal-vault"
    _seed_ingested(personal, "notes.md", "# Notes\n\nbody\n")

    result = CliRunner().invoke(cli, ["raw", "personal:notes"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(personal / "raw" / "notes.md")


def test_raw_unqualified_unique_resolves_across_vaults(two_vault_config, tmp_path):
    work = tmp_path / "work-vault"
    _seed_ingested(work, "only-work.md", "# Only Work\n\nbody\n")

    result = CliRunner().invoke(cli, ["raw", "only-work"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(work / "raw" / "only-work.md")


def test_raw_unqualified_ambiguous_lists_qualified_candidates(two_vault_config, tmp_path):
    _seed_ingested(tmp_path / "work-vault", "dup.md", "# Dup\n\nw\n")
    _seed_ingested(tmp_path / "personal-vault", "dup.md", "# Dup\n\np\n")

    result = CliRunner().invoke(cli, ["raw", "dup"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "work:dup" in combined
    assert "personal:dup" in combined


def test_reingest_qualified_and_unqualified_across_vaults(two_vault_config, tmp_path):
    personal = tmp_path / "personal-vault"
    raw_path = _seed_ingested(personal, "evolve.md", "# Evolve\n\nfirst\n")

    raw_path.write_text("# Evolve\n\nsecond\n")
    result = CliRunner().invoke(cli, ["reingest", "personal:evolve"])
    assert result.exit_code == 0, result.output
    assert "second" in (personal / "research" / "evolve.md").read_text()

    raw_path.write_text("# Evolve\n\nthird\n")
    result = CliRunner().invoke(cli, ["reingest", "evolve"])
    assert result.exit_code == 0, result.output
    assert "third" in (personal / "research" / "evolve.md").read_text()


def test_search_spans_vaults_with_qualified_pasteable_paths(two_vault_config, tmp_path):
    _seed_ingested(tmp_path / "work-vault", "zeb-work.md",
                   "# Zeb Work\n\nzebras migrate seasonally\n")
    _seed_ingested(tmp_path / "personal-vault", "zeb-home.md",
                   "# Zeb Home\n\nzebras in the garden\n")

    result = CliRunner().invoke(cli, ["search", "zebras"])
    assert result.exit_code == 0, result.output
    assert "work:research/zeb-work.md" in result.output
    assert "personal:research/zeb-home.md" in result.output

    # A printed path pastes directly into show.
    shown = CliRunner().invoke(cli, ["show", "personal:research/zeb-home.md"])
    assert shown.exit_code == 0, shown.output
    assert "garden" in shown.stdout


def test_search_topic_accepts_vault_prefix(two_vault_config, tmp_path):
    _seed_ingested(tmp_path / "work-vault", "lion-work.md",
                   "# Lion Work\n\nlions roam\n")
    _seed_ingested(tmp_path / "personal-vault", "lion-home.md",
                   "# Lion Home\n\nlions sleep\n")

    result = CliRunner().invoke(cli, ["search", "lions",
                                      "--topic", "personal:research"])
    assert result.exit_code == 0, result.output
    assert "personal:research/lion-home.md" in result.output
    assert "lion-work.md" not in result.output


def test_search_no_results_across_vaults(two_vault_config):
    result = CliRunner().invoke(cli, ["search", "xylophone"])
    assert result.exit_code == 0, result.output
    assert "No results found." in result.output


# --- multi-vault lint ----------------------------------------------------------

def _seed_broken_link(vault, slug="linker"):
    meta = {"title": slug.title(), "topic": "research", "tags": [],
            "created": "2026-01-01", "updated": "2026-01-01", "sources": []}
    (vault / "research" / f"{slug}.md").write_text(
        render_page(meta, f"# {slug.title()}\n\nSee [[No Such Page]].\n"))


def test_lint_multi_vault_prints_labeled_sections(two_vault_config, tmp_path):
    _seed_broken_link(tmp_path / "work-vault")

    result = CliRunner().invoke(cli, ["lint"])
    assert result.exit_code == 0, result.output
    assert "vault: work" in result.output
    assert "vault: personal" in result.output
    assert "[LINK]" in result.output


def test_lint_vault_flag_narrows_to_one(two_vault_config, tmp_path):
    _seed_broken_link(tmp_path / "work-vault")

    result = CliRunner().invoke(cli, ["--vault", "work", "lint"])
    assert result.exit_code == 0, result.output
    assert "[LINK]" in result.output
    assert "vault: work" not in result.output  # single-vault output shape


def test_lint_strict_exits_nonzero_on_second_vault_tag_finding(
    two_vault_config, tmp_path
):
    # sorted order is (personal, work): put the TAG finding in work, the
    # second section, and keep personal vocabulary-free (tag audit inert).
    work = tmp_path / "work-vault"
    wiki = yaml.safe_load((work / "wiki.yaml").read_text())
    wiki["tags"] = {"mode": "warn", "vocabulary": {"Python": ["py"]}}
    (work / "wiki.yaml").write_text(yaml.dump(wiki))
    meta = {"title": "Tagged", "topic": "research", "tags": ["py"],
            "created": "2026-01-01", "updated": "2026-01-01", "sources": []}
    (work / "research" / "tagged.md").write_text(
        render_page(meta, "# Tagged\n\nbody\n"))

    result = CliRunner().invoke(cli, ["lint"])
    assert result.exit_code == 0, result.output

    result = CliRunner().invoke(cli, ["lint", "--strict"])
    assert result.exit_code != 0
    assert "[TAG]" in result.output


def test_lint_unsupported_vault_skips_with_notice_and_exit_zero(
    tmp_path, monkeypatch
):
    from conftest import make_vault
    work = make_vault(tmp_path / "work-vault")
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({
        "vaults": {
            "work": {"path": str(work)},
            "team": {"url": "http://127.0.0.1:9", "token": None},
        }
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    result = CliRunner().invoke(cli, ["lint"])
    assert result.exit_code == 0, result.output
    assert "vault: team" in result.output
    assert "skipped" in result.output
    assert "vault: work" in result.output


# --- topic-driven ingest routing ----------------------------------------------

def _routing_config(two_vault_config, tmp_path):
    """Make 'work' the default vault and declare topic 'journal' only in
    personal's wiki.yaml. Returns (work, personal)."""
    work = tmp_path / "work-vault"
    personal = tmp_path / "personal-vault"
    cfg = yaml.safe_load(two_vault_config.read_text())
    cfg["default_vault"] = "work"
    two_vault_config.write_text(yaml.dump(cfg))
    wiki = yaml.safe_load((personal / "wiki.yaml").read_text())
    wiki["topics"].append("journal")
    (personal / "wiki.yaml").write_text(yaml.dump(wiki))
    (personal / "journal").mkdir()
    return work, personal


def test_ingest_topic_routes_to_unique_declaring_vault(
    two_vault_config, tmp_path
):
    work, personal = _routing_config(two_vault_config, tmp_path)
    src = tmp_path / "entry.md"
    src.write_text("# Entry\n\ndear diary\n")

    result = CliRunner().invoke(cli, ["ingest", str(src), "--topic", "journal"])
    assert result.exit_code == 0, result.output
    assert (personal / "journal" / "entry.md").exists()
    assert not (work / "journal" / "entry.md").exists()


def test_ingest_undeclared_topic_falls_back_to_default_vault(
    two_vault_config, tmp_path
):
    work, personal = _routing_config(two_vault_config, tmp_path)
    src = tmp_path / "odd.md"
    src.write_text("# Odd\n\nbody\n")

    result = CliRunner().invoke(cli, ["ingest", str(src), "--topic", "misc"])
    assert result.exit_code == 0, result.output
    assert (work / "misc" / "odd.md").exists()


def test_ingest_no_topic_lands_in_default_vault_default_topic(
    two_vault_config, tmp_path
):
    work, _personal = _routing_config(two_vault_config, tmp_path)
    src = tmp_path / "plain.md"
    src.write_text("# Plain\n\nbody\n")

    result = CliRunner().invoke(cli, ["ingest", str(src)])
    assert result.exit_code == 0, result.output
    assert (work / "research" / "plain.md").exists()


def test_ingest_doubly_declared_topic_is_loud_and_resolvable(
    two_vault_config, tmp_path
):
    work, personal = _routing_config(two_vault_config, tmp_path)
    src = tmp_path / "shared.md"
    src.write_text("# Shared\n\nbody\n")

    # 'research' is declared by both vaults: hard error naming them.
    result = CliRunner().invoke(cli, ["ingest", str(src), "--topic", "research"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "work" in combined and "personal" in combined

    # --vault resolves it.
    result = CliRunner().invoke(
        cli, ["--vault", "personal", "ingest", str(src), "--topic", "research"])
    assert result.exit_code == 0, result.output
    assert (personal / "research" / "shared.md").exists()

    # A vault: prefix on the topic resolves it too.
    src2 = tmp_path / "shared2.md"
    src2.write_text("# Shared Two\n\nbody\n")
    result = CliRunner().invoke(
        cli, ["ingest", str(src2), "--topic", "work:research"])
    assert result.exit_code == 0, result.output
    assert (work / "research" / "shared-two.md").exists()


def test_reingest_unqualified_ambiguous_is_loud(two_vault_config, tmp_path):
    _seed_ingested(tmp_path / "work-vault", "both.md", "# Both\n\nw\n")
    _seed_ingested(tmp_path / "personal-vault", "both.md", "# Both\n\np\n")

    result = CliRunner().invoke(cli, ["reingest", "both"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "work:both" in combined
    assert "personal:both" in combined


# --- init --name ---------------------------------------------------------------

def test_init_name_flag_creates_and_registers_named_vault(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    vault_path = tmp_path / "personal-wiki"
    result = CliRunner().invoke(
        cli, ["init", str(vault_path), "--name", "personal"])
    assert result.exit_code == 0, result.output
    assert (vault_path / "wiki.yaml").is_file()

    listed = CliRunner().invoke(cli, ["vault", "list"])
    assert listed.exit_code == 0, listed.output
    assert "personal" in listed.output


def test_init_name_rejects_illegal_and_duplicate_names(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))

    bad = CliRunner().invoke(
        cli, ["init", str(tmp_path / "v1"), "--name", "Bad Name"])
    assert bad.exit_code != 0
    assert not (tmp_path / "v1").exists()

    assert CliRunner().invoke(
        cli, ["init", str(tmp_path / "v2"), "--name", "personal"]
    ).exit_code == 0
    dup = CliRunner().invoke(
        cli, ["init", str(tmp_path / "v3"), "--name", "personal"])
    assert dup.exit_code != 0
    assert not (tmp_path / "v3").exists()
