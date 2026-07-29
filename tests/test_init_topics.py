"""Topic-aware init: --topics, subsequent-vault behavior, ingest guard.

A second vault must not silently duplicate DEFAULT_TOPICS — duplicated topics
make every unqualified --topic ambiguous across vaults. Subsequent inits
prompt for topics on a TTY, default to none otherwise, and a vault without a
default_topic refuses untargeted ingest instead of minting "research".
"""

import yaml
import pytest
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.vault import init_vault, DEFAULT_TOPICS
from conftest import make_vault


def _wiki_config(vault_path):
    return yaml.safe_load((vault_path / "wiki.yaml").read_text())


def _own_config(tmp_path, monkeypatch, data=None):
    """Point AGENT_WIKI_CONFIG_DIR at a per-test config dir, optionally seeded."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (config_dir / "config.yaml").write_text(yaml.dump(data))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return config_dir


# --- init_vault(topics=...) ----------------------------------------------------

def test_init_vault_with_topics(tmp_path):
    vault = tmp_path / "v"
    init_vault(vault, topics=["notes", "ideas"])

    config = _wiki_config(vault)
    assert config["topics"] == ["notes", "ideas"]
    assert config["default_topic"] == "notes"
    assert (vault / "notes").is_dir()
    assert (vault / "ideas").is_dir()
    assert not (vault / "projects").exists()
    assert not (vault / "sessions").exists()


def test_init_vault_with_empty_topics(tmp_path):
    vault = tmp_path / "v"
    init_vault(vault, topics=[])

    config = _wiki_config(vault)
    assert config["topics"] == []
    assert "default_topic" not in config
    # The conversations block stays regardless of topics: doctor's
    # conversations-block check re-adds it whenever it is missing.
    assert config["conversations"]["topic"] == "sessions"
    assert (vault / "raw").is_dir()
    assert (vault / "index.md").exists()


# --- awiki init --topics -------------------------------------------------------

def test_init_topics_flag_first_vault(tmp_path, monkeypatch):
    _own_config(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "v"), "--topics", "notes, ideas"])
    assert result.exit_code == 0, result.output

    config = _wiki_config(tmp_path / "v")
    assert config["topics"] == ["notes", "ideas"]
    assert config["default_topic"] == "notes"


def test_init_topics_flag_rejects_non_slug(tmp_path, monkeypatch):
    _own_config(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "v"), "--topics", "Bad Topic"])
    assert result.exit_code != 0
    assert "slug" in result.stderr.lower()
    assert not (tmp_path / "v" / "wiki.yaml").exists()


# --- subsequent-vault init -----------------------------------------------------

def test_second_vault_noninteractive_gets_no_topics_and_warns(tmp_path, monkeypatch):
    first = make_vault(tmp_path / "first")
    _own_config(tmp_path, monkeypatch, {"vaults": {"main": {"path": str(first)}}})

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "second"), "--name", "second"])
    assert result.exit_code == 0, result.output

    config = _wiki_config(tmp_path / "second")
    assert config["topics"] == []
    assert "default_topic" not in config
    assert "no topics" in result.stderr


def test_second_vault_tty_prompts_for_topics(tmp_path, monkeypatch):
    import agent_wiki.cli as cli_mod

    first = make_vault(tmp_path / "first")
    _own_config(tmp_path, monkeypatch, {"vaults": {"main": {"path": str(first)}}})
    monkeypatch.setattr(cli_mod, "_stdin_isatty", lambda: True)

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "second"), "--name", "second"],
        input="notes, ideas\n")
    assert result.exit_code == 0, result.output

    config = _wiki_config(tmp_path / "second")
    assert config["topics"] == ["notes", "ideas"]
    assert (tmp_path / "second" / "notes").is_dir()


def test_second_vault_tty_empty_answer_means_no_topics(tmp_path, monkeypatch):
    import agent_wiki.cli as cli_mod

    first = make_vault(tmp_path / "first")
    _own_config(tmp_path, monkeypatch, {"vaults": {"main": {"path": str(first)}}})
    monkeypatch.setattr(cli_mod, "_stdin_isatty", lambda: True)

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "second"), "--name", "second"],
        input="\n")
    assert result.exit_code == 0, result.output

    config = _wiki_config(tmp_path / "second")
    assert config["topics"] == []
    assert "no topics" in result.stderr


def test_first_vault_keeps_default_topics_without_prompt(tmp_path, monkeypatch):
    import agent_wiki.cli as cli_mod

    _own_config(tmp_path, monkeypatch)
    # A wrongly-firing prompt would hit EOF (no input piped) and abort.
    monkeypatch.setattr(cli_mod, "_stdin_isatty", lambda: True)

    result = CliRunner().invoke(cli, ["init", str(tmp_path / "v")])
    assert result.exit_code == 0, result.output

    config = _wiki_config(tmp_path / "v")
    assert config["topics"] == DEFAULT_TOPICS
    assert config["default_topic"] == "research"


def test_bare_reinit_over_legacy_config_keeps_default_topics(tmp_path, monkeypatch):
    """A legacy bare init REPLACES the registered vault rather than adding a
    second one, so it keeps the out-of-box default topics."""
    first = make_vault(tmp_path / "first")
    _own_config(tmp_path, monkeypatch, {"vault_path": str(first)})

    result = CliRunner().invoke(cli, ["init", str(tmp_path / "second")])
    assert result.exit_code == 0, result.output

    config = _wiki_config(tmp_path / "second")
    assert config["topics"] == DEFAULT_TOPICS


def test_topics_colliding_with_another_vault_warn(tmp_path, monkeypatch):
    first = make_vault(tmp_path / "first")  # declares research
    _own_config(tmp_path, monkeypatch, {"vaults": {"main": {"path": str(first)}}})

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "second"), "--name", "second",
              "--topics", "research, notes"])
    assert result.exit_code == 0, result.output
    assert "research" in result.stderr
    assert "main" in result.stderr


# --- reclaiming a stale registry name ------------------------------------------

def test_init_reclaims_named_entry_whose_vault_dir_is_missing(tmp_path, monkeypatch):
    """A registry entry whose local vault is gone (no wiki.yaml at its path)
    must not block re-initing under the same name."""
    gone = tmp_path / "personal"  # never created
    _own_config(tmp_path, monkeypatch, {"vaults": {"personal": {"path": str(gone)}}})

    result = CliRunner().invoke(
        cli, ["init", "--name", "personal", "--topics", "health,dreams", str(gone)])
    assert result.exit_code == 0, result.output

    config = _wiki_config(gone)
    assert config["topics"] == ["health", "dreams"]
    assert config["default_topic"] == "health"

    persisted = yaml.safe_load(
        (tmp_path / "config" / "config.yaml").read_text())
    assert persisted["vaults"]["personal"]["path"] == str(gone)


def test_init_duplicate_name_with_live_vault_still_errors(tmp_path, monkeypatch):
    live = make_vault(tmp_path / "personal")
    _own_config(tmp_path, monkeypatch, {"vaults": {"personal": {"path": str(live)}}})

    result = CliRunner().invoke(
        cli, ["init", "--name", "personal", str(tmp_path / "elsewhere")])
    assert result.exit_code != 0
    assert "already configured" in result.stderr
    assert not (tmp_path / "elsewhere").exists()


def test_init_duplicate_name_remote_entry_still_errors(tmp_path, monkeypatch):
    _own_config(tmp_path, monkeypatch, {"vaults": {
        "personal": {"url": "http://example.invalid:8731", "token": "t"}}})

    result = CliRunner().invoke(
        cli, ["init", "--name", "personal", str(tmp_path / "local")])
    assert result.exit_code != 0
    assert "already configured" in result.stderr


# --- ingest guard: no default_topic ---------------------------------------------

def _topicless_vault(tmp_path):
    """A minimal vault declaring no topics and no default_topic — built by
    hand, since make_vault pre-creates topic folders."""
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "wiki.yaml").write_text(yaml.dump({
        "vault": {"name": "v", "version": 1},
        "topics": [],
    }))
    (vault / "raw").mkdir()
    (vault / "index.md").write_text("# Index\n")
    (vault / "log.md").write_text("# Activity Log\n")
    return vault


def test_ingest_without_topic_errors_when_no_default_topic(tmp_path, monkeypatch):
    vault = _topicless_vault(tmp_path)
    _own_config(tmp_path, monkeypatch, {"vault_path": str(vault)})
    note = tmp_path / "note.md"
    note.write_text("# A Note\n\nbody\n")

    result = CliRunner().invoke(cli, ["ingest", str(note)])
    assert result.exit_code != 0
    assert "default_topic" in result.stderr
    # Pre-flight: the failed ingest must leave the vault untouched.
    assert not (vault / "research").exists()
    assert not (vault / "raw" / "note.md").exists()


def test_ingest_with_explicit_topic_works_without_default_topic(tmp_path, monkeypatch):
    vault = _topicless_vault(tmp_path)
    _own_config(tmp_path, monkeypatch, {"vault_path": str(vault)})
    note = tmp_path / "note.md"
    note.write_text("# A Note\n\nbody\n")

    result = CliRunner().invoke(cli, ["ingest", str(note), "--topic", "notes"])
    assert result.exit_code == 0, result.output
    assert (vault / "notes" / "a-note.md").exists()


def test_update_errors_when_no_topic_anywhere(tmp_path):
    """--update with no --topic, no topic in the existing page's frontmatter,
    and no default_topic is a hard error, not a silent 'research' fallback."""
    from agent_wiki.ingest import ingest_file
    from agent_wiki.page import parse_page, update_frontmatter

    # "notes" must be a declared topic so the update resolves the linked page;
    # only default_topic is absent.
    vault = make_vault(tmp_path / "v")
    (vault / "wiki.yaml").write_text(yaml.dump({
        "vault": {"name": "v", "version": 1},
        "topics": ["notes"],
    }))
    (vault / "notes").mkdir()
    note = tmp_path / "note.md"
    note.write_text("# A Note\n\nbody\n")
    page = ingest_file(note, vault, topic="notes")

    meta = parse_page(page)["meta"]
    del meta["topic"]
    update_frontmatter(page, meta)

    with pytest.raises(ValueError, match="default_topic"):
        ingest_file(note, vault, update=True)
