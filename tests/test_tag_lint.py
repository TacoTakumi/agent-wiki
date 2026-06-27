"""tag-audit lint check (REQ-12, REQ-02).

A read-only check flagging alias tags (fixable) and novel out-of-vocabulary tags
per page, plus vocabulary conflicts — inert when no vocabulary is configured."""

import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.config import load_vault_config
from agent_wiki.lint import LINT_TYPES, lint_vault
from agent_wiki.page import render_page, slugify


def _set_vocab(vault, vocabulary, mode="warn"):
    config = load_vault_config(vault)
    config["tags"] = {"mode": mode, "vocabulary": vocabulary}
    (vault / "wiki.yaml").write_text(yaml.dump(config))


def _page(vault, topic, title, tags):
    path = vault / topic / f"{slugify(title)}.md"
    path.write_text(render_page({"title": title, "topic": topic, "tags": tags}, "body\n"))


def _audit(vault):
    return [i for i in lint_vault(vault) if i["type"] == "tag_audit"]


def test_tag_audit_type_is_registered():
    assert "tag_audit" in LINT_TYPES


def test_flags_exactly_the_alias_and_novel_pages(tmp_vault):
    _set_vocab(tmp_vault, {"stt": ["asr"]})
    _page(tmp_vault, "research", "Alias Page", ["asr"])
    _page(tmp_vault, "research", "Novel Page", ["wibble"])
    # A page already on a preferred term is not flagged.
    _page(tmp_vault, "research", "Clean Page", ["stt"])

    findings = _audit(tmp_vault)
    assert len(findings) == 2

    blob = " | ".join(f["detail"] for f in findings)
    assert "asr" in blob and "stt" in blob   # the fixable alias finding
    assert "wibble" in blob                   # the novel finding


def test_inert_without_vocabulary(tmp_vault):
    # tmp_vault has no tags block.
    _page(tmp_vault, "research", "Some Page", ["asr", "wibble"])
    assert _audit(tmp_vault) == []


def test_mode_off_is_inert(tmp_vault):
    _set_vocab(tmp_vault, {"stt": ["asr"]}, mode="off")
    _page(tmp_vault, "research", "Alias Page", ["asr"])
    assert _audit(tmp_vault) == []


def test_reports_vocabulary_conflict(tmp_vault):
    # 'asr' is claimed by two preferred terms — an ambiguous vocabulary.
    _set_vocab(tmp_vault, {"stt": ["asr"], "speech": ["asr"]})
    findings = _audit(tmp_vault)

    conflict = [f for f in findings if "asr" in f["detail"] and "wiki.yaml" in f["path"]]
    assert conflict, findings


# --- lint --strict CI gate (REQ-13) ------------------------------------------


def test_strict_gates_exit_code_only(tmp_config, tmp_vault):
    _set_vocab(tmp_vault, {"stt": ["asr"]})
    _page(tmp_vault, "research", "Alias Page", ["asr"])

    runner = CliRunner()
    plain = runner.invoke(cli, ["lint"])
    strict = runner.invoke(cli, ["lint", "--strict"])

    assert plain.exit_code == 0
    assert strict.exit_code != 0
    # The flag changes only the exit code: identical findings are printed.
    assert plain.output == strict.output


def test_strict_passes_when_no_tag_audit_findings(tmp_config, tmp_vault):
    # A configured vocabulary with a clean page yields no tag-audit findings.
    _set_vocab(tmp_vault, {"stt": ["asr"]})
    _page(tmp_vault, "research", "Clean Page", ["stt"])

    result = CliRunner().invoke(cli, ["lint", "--strict"])
    assert result.exit_code == 0
