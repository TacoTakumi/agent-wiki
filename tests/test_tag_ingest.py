import yaml
from pathlib import Path

from click.testing import CliRunner

from agent_wiki.ingest import ingest_file, ingest_extracted
from agent_wiki.page import parse_page
from agent_wiki.cli import cli


def _set_vocab(vault, mode="warn", vocabulary=None):
    config = yaml.safe_load((vault / "wiki.yaml").read_text())
    config["tags"] = {"mode": mode, "vocabulary": vocabulary or {"stt": ["asr"]}}
    (vault / "wiki.yaml").write_text(yaml.dump(config))


def _src(tmp_path, name="doc.md", body="# Doc\n\nBody.\n"):
    p = tmp_path / name
    p.write_text(body)
    return p


# --- warn mode: alias remap --------------------------------------------------


def test_warn_alias_is_canonicalized_and_announced(tmp_vault, tmp_path, capsys):
    _set_vocab(tmp_vault)
    page = ingest_file(_src(tmp_path), tmp_vault, topic="research", tags=["asr"])
    assert parse_page(page)["meta"]["tags"] == ["stt"]
    out = capsys.readouterr().out
    assert "asr" in out and "stt" in out


def test_warn_preferred_casing_normalized_without_announce(tmp_vault, tmp_path, capsys):
    _set_vocab(tmp_vault)
    page = ingest_file(_src(tmp_path), tmp_vault, topic="research", tags=["STT"])
    assert parse_page(page)["meta"]["tags"] == ["stt"]
    assert "canonicaliz" not in capsys.readouterr().out.lower()


# --- warn mode: novel tag ----------------------------------------------------


def test_warn_novel_tag_kept_and_warned(tmp_vault, tmp_path, capsys):
    _set_vocab(tmp_vault)
    page = ingest_file(_src(tmp_path), tmp_vault, topic="research", tags=["frobnicate"])
    assert parse_page(page)["meta"]["tags"] == ["frobnicate"]
    out = capsys.readouterr().out
    assert "frobnicate" in out and "warning" in out.lower()


# --- --update reingest path canonicalizes existing tags ----------------------


def test_reingest_rewrites_existing_alias_tag(tmp_vault, tmp_path):
    # Ingest while inert so the page is written bearing the raw alias 'asr'.
    src = _src(tmp_path)
    page = ingest_file(src, tmp_vault, topic="research", tags=["asr"])
    assert parse_page(page)["meta"]["tags"] == ["asr"]
    # Now configure the vocabulary and reingest from raw (tags=None -> reuse).
    _set_vocab(tmp_vault)
    raw = tmp_vault / "raw" / "doc.md"
    page = ingest_file(raw, tmp_vault, update=True, tags=None)
    assert parse_page(page)["meta"]["tags"] == ["stt"]


# --- URL ingest inherits canonicalization via ingest_extracted ---------------


def test_url_ingest_canonicalizes_identically(tmp_vault):
    _set_vocab(tmp_vault)
    page = ingest_extracted(
        tmp_vault,
        source_url="https://example.com/post",
        content_type="text/html",
        asset=b"<html></html>",
        markdown="# Post\n\nBody.\n",
        tags=["asr", "stt"],
    )
    assert parse_page(page)["meta"]["tags"] == ["stt"]


# --- inert when unconfigured (REQ-07) ----------------------------------------


def test_no_tags_block_is_inert(tmp_vault, tmp_path, capsys):
    page = ingest_file(_src(tmp_path), tmp_vault, topic="research",
                       tags=["asr", "frobnicate"])
    # untouched: no canonicalization, no dedup, order preserved
    assert parse_page(page)["meta"]["tags"] == ["asr", "frobnicate"]
    assert capsys.readouterr().out == ""


def test_mode_off_is_inert(tmp_vault, tmp_path, capsys):
    _set_vocab(tmp_vault, mode="off")
    page = ingest_file(_src(tmp_path), tmp_vault, topic="research", tags=["asr"])
    assert parse_page(page)["meta"]["tags"] == ["asr"]
    assert capsys.readouterr().out == ""


# --- CLI end-to-end ----------------------------------------------------------


def test_cli_ingest_announces_remap(tmp_vault, tmp_config, tmp_path):
    _set_vocab(tmp_vault)
    src = _src(tmp_path, name="cli-doc.md", body="# CLI Doc\n\nBody.\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(src), "--tags", "asr"])
    assert result.exit_code == 0
    assert "asr" in result.output and "stt" in result.output
    page = parse_page(tmp_vault / "research" / "cli-doc.md")
    assert page["meta"]["tags"] == ["stt"]
