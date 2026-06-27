"""CLI tests for `awiki tag fix` (REQ-15, REQ-16).

`tag fix` canonicalizes frontmatter tags across the topic folders against the
wiki.yaml vocabulary: preview by default (reports, writes nothing); `--write`
rewrites only the frontmatter tag list, leaving the page body byte-identical and
`raw/` untouched; known aliases canonicalize, novel out-of-vocab tags are
reported but left unchanged. Driven through the real CLI against tmp_config."""

import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.ingest import ingest_file
from agent_wiki.page import parse_page, render_page, slugify


def _set_vocab(vault, mode="warn", vocabulary=None):
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    config["tags"] = {"mode": mode, "vocabulary": vocabulary or {"stt": ["asr"]}}
    (vault / "wiki.yaml").write_text(yaml.dump(config))


def _page(vault, topic, title, tags):
    path = vault / topic / f"{slugify(title)}.md"
    path.write_text(render_page({"title": title, "topic": topic, "tags": tags},
                                "Body line.\n"))
    return path


def test_preview_reports_alias_page_and_writes_nothing(tmp_config, tmp_vault):
    page = _page(tmp_vault, "research", "Alias Page", ["asr"])
    _set_vocab(tmp_vault)
    before = page.read_text()

    result = CliRunner().invoke(cli, ["tag", "fix"])
    assert result.exit_code == 0, result.output
    # The alias-bearing page and its remap are named in the preview.
    assert str(page.relative_to(tmp_vault)) in result.output
    assert "asr" in result.output and "stt" in result.output
    # Preview writes nothing: the page is byte-unchanged.
    assert page.read_text() == before


def test_write_canonicalizes_alias_empty_body_diff_raw_unchanged(
        tmp_config, tmp_vault, tmp_path):
    # Ingest while inert (no vocab yet) so the page has a real raw source and
    # is written bearing the alias 'asr'.
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n\nThe body.\n")
    page = ingest_file(src, tmp_vault, topic="research", tags=["asr"])
    raw = tmp_vault / "raw" / "doc.md"
    body_before = parse_page(page)["body"]
    raw_before = raw.read_text()

    _set_vocab(tmp_vault)
    result = CliRunner().invoke(cli, ["tag", "fix", "--write"])
    assert result.exit_code == 0, result.output

    parsed = parse_page(page)
    assert parsed["meta"]["tags"] == ["stt"]   # alias canonicalized to preferred
    assert parsed["body"] == body_before       # page body byte-identical
    assert raw.read_text() == raw_before        # raw/ untouched


def test_novel_tag_reported_and_unchanged_after_write(tmp_config, tmp_vault):
    page = _page(tmp_vault, "research", "Novel Page", ["frobnicate"])
    _set_vocab(tmp_vault)
    before = page.read_text()

    preview = CliRunner().invoke(cli, ["tag", "fix"])
    assert preview.exit_code == 0, preview.output
    assert "frobnicate" in preview.output      # novel tag is reported

    written = CliRunner().invoke(cli, ["tag", "fix", "--write"])
    assert written.exit_code == 0, written.output
    # Novel tag is never auto-changed: still present, page byte-unchanged.
    assert parse_page(page)["meta"]["tags"] == ["frobnicate"]
    assert page.read_text() == before


def test_alias_canonicalized_and_novel_preserved_on_same_page(tmp_config, tmp_vault):
    page = _page(tmp_vault, "research", "Mixed Page", ["asr", "frobnicate"])
    _set_vocab(tmp_vault)

    result = CliRunner().invoke(cli, ["tag", "fix", "--write"])
    assert result.exit_code == 0, result.output
    # The known alias canonicalizes; the novel tag rides through unchanged.
    assert parse_page(page)["meta"]["tags"] == ["stt", "frobnicate"]


def test_inert_without_vocabulary(tmp_config, tmp_vault):
    page = _page(tmp_vault, "research", "Some Page", ["asr"])
    before = page.read_text()

    result = CliRunner().invoke(cli, ["tag", "fix", "--write"])
    assert result.exit_code == 0, result.output
    # No vocabulary configured → nothing canonicalizes, page untouched.
    assert page.read_text() == before


def test_raw_and_root_artifacts_are_not_rewritten(tmp_config, tmp_vault):
    # A stray frontmatter file under raw/ with an alias tag must be ignored: the
    # pass excludes raw/, index.md, and log.md.
    raw_doc = tmp_vault / "raw" / "stray.md"
    raw_doc.write_text(render_page({"title": "Stray", "tags": ["asr"]}, "x\n"))
    raw_before = raw_doc.read_text()
    _set_vocab(tmp_vault)

    result = CliRunner().invoke(cli, ["tag", "fix", "--write"])
    assert result.exit_code == 0, result.output
    assert raw_doc.read_text() == raw_before


# --- scope narrowing: --topic / path argument (REQ-17) -----------------------


def test_fix_topic_narrows_to_that_topic(tmp_config, tmp_vault):
    research = _page(tmp_vault, "research", "R Page", ["asr"])
    decisions = _page(tmp_vault, "decisions", "D Page", ["asr"])
    _set_vocab(tmp_vault)
    dec_before = decisions.read_text()

    result = CliRunner().invoke(cli, ["tag", "fix", "--write", "--topic", "research"])
    assert result.exit_code == 0, result.output
    # research/ is canonicalized; the other topic is left byte-unchanged.
    assert parse_page(research)["meta"]["tags"] == ["stt"]
    assert decisions.read_text() == dec_before
    assert parse_page(decisions)["meta"]["tags"] == ["asr"]


def test_fix_path_argument_narrows_to_that_topic(tmp_config, tmp_vault):
    research = _page(tmp_vault, "research", "R Page", ["asr"])
    decisions = _page(tmp_vault, "decisions", "D Page", ["asr"])
    _set_vocab(tmp_vault)
    dec_before = decisions.read_text()

    # A vault-relative path argument narrows the pass the same way --topic does.
    result = CliRunner().invoke(cli, ["tag", "fix", "--write", "research"])
    assert result.exit_code == 0, result.output
    assert parse_page(research)["meta"]["tags"] == ["stt"]
    assert decisions.read_text() == dec_before


def test_fix_unknown_topic_errors(tmp_config, tmp_vault):
    page = _page(tmp_vault, "research", "R Page", ["asr"])
    _set_vocab(tmp_vault)
    before = page.read_text()

    result = CliRunner().invoke(cli, ["tag", "fix", "--write", "--topic", "nope"])
    assert result.exit_code != 0
    assert "nope" in result.output
    assert page.read_text() == before   # nothing written on a bad narrowing


def test_fix_rejects_both_topic_and_path(tmp_config, tmp_vault):
    _set_vocab(tmp_vault)
    result = CliRunner().invoke(
        cli, ["tag", "fix", "--topic", "research", "research"])
    assert result.exit_code != 0
