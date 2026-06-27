"""CLI tests for the `awiki tag` command group.

These cover `tag add` (REQ-09 idempotent persistence, REQ-02 conflict refusal)
and `tag suggest` (REQ-10 draft block + --write merge), driving the real CLI
through CliRunner against the tmp_config vault."""

import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.config import load_vault_config, parse_tag_vocabulary
from agent_wiki.page import render_page, slugify


def _vocab(vault):
    return parse_tag_vocabulary(load_vault_config(vault))


def _page(vault, topic, title, tags):
    """Write a frontmatter page with the given tags into a topic folder."""
    path = vault / topic / f"{slugify(title)}.md"
    path.write_text(render_page({"title": title, "topic": topic, "tags": tags}, "body\n"))


def _all_tags(vocab):
    """Every tag the vocabulary covers — preferred keys plus alias values."""
    covered = set(vocab.vocabulary)
    for aliases in vocab.vocabulary.values():
        covered.update(aliases)
    return covered


def test_add_persists_preferred_and_alias(tmp_config, tmp_vault):
    result = CliRunner().invoke(cli, ["tag", "add", "stt", "--alias", "asr"])
    assert result.exit_code == 0, result.output

    vocab = _vocab(tmp_vault)
    assert vocab.vocabulary == {"stt": ["asr"]}


def test_add_is_idempotent(tmp_config, tmp_vault):
    runner = CliRunner()
    first = runner.invoke(cli, ["tag", "add", "stt", "--alias", "asr"])
    assert first.exit_code == 0, first.output
    after_first = (tmp_vault / "wiki.yaml").read_text()

    second = runner.invoke(cli, ["tag", "add", "stt", "--alias", "asr"])
    assert second.exit_code == 0, second.output
    after_second = (tmp_vault / "wiki.yaml").read_text()

    # Re-adding the same term/alias leaves wiki.yaml byte-identical.
    assert after_second == after_first


def test_add_refuses_conflicting_alias_and_writes_nothing(tmp_config, tmp_vault):
    runner = CliRunner()
    runner.invoke(cli, ["tag", "add", "stt", "--alias", "asr"])
    before = (tmp_vault / "wiki.yaml").read_text()

    # 'asr' is already bound to 'stt'; binding it to 'foo' is a conflict.
    result = runner.invoke(cli, ["tag", "add", "foo", "--alias", "asr"])
    assert result.exit_code != 0
    assert "asr" in result.output

    # Nothing was written: the file is unchanged and 'foo' never appears.
    assert (tmp_vault / "wiki.yaml").read_text() == before
    assert "foo" not in _vocab(tmp_vault).vocabulary


# --- tag suggest -------------------------------------------------------------


def _seed_tagged_pages(vault):
    """gpu (x3) + gpu-split + tensor-split form one obvious cluster; stt/asr are
    separate. Returns the set of all in-use tags."""
    _page(vault, "research", "Page A", ["gpu"])
    _page(vault, "research", "Page B", ["gpu"])
    _page(vault, "research", "Page C", ["gpu", "gpu-split"])
    _page(vault, "research", "Page D", ["tensor-split"])
    _page(vault, "decisions", "Page E", ["stt", "asr"])
    return {"gpu", "gpu-split", "tensor-split", "stt", "asr"}


def test_suggest_prints_valid_yaml_covering_all_tags(tmp_config, tmp_vault):
    in_use = _seed_tagged_pages(tmp_vault)
    before = (tmp_vault / "wiki.yaml").read_text()

    result = CliRunner().invoke(cli, ["tag", "suggest"])
    assert result.exit_code == 0, result.output

    # The printed draft parses as YAML and covers every in-use tag.
    vocab = parse_tag_vocabulary(yaml.safe_load(result.output))
    assert in_use <= _all_tags(vocab)

    # It changed nothing on disk.
    assert (tmp_vault / "wiki.yaml").read_text() == before


def test_suggest_groups_obvious_clusters(tmp_config, tmp_vault):
    _seed_tagged_pages(tmp_vault)

    result = CliRunner().invoke(cli, ["tag", "suggest"])
    vocab = parse_tag_vocabulary(yaml.safe_load(result.output))

    # gpu (most frequent) is the preferred term; the related hyphen-token tags
    # are grouped under it as aliases, not left as separate preferred terms.
    assert "gpu" in vocab.vocabulary
    assert set(vocab.vocabulary["gpu"]) >= {"gpu-split", "tensor-split"}
    assert "gpu-split" not in vocab.vocabulary
    assert "tensor-split" not in vocab.vocabulary


def test_suggest_write_updates_the_tags_block(tmp_config, tmp_vault):
    in_use = _seed_tagged_pages(tmp_vault)

    result = CliRunner().invoke(cli, ["tag", "suggest", "--write"])
    assert result.exit_code == 0, result.output

    vocab = _vocab(tmp_vault)
    assert in_use <= _all_tags(vocab)
