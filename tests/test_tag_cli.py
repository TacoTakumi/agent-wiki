"""CLI tests for the `awiki tag` command group.

These cover `tag add` (REQ-09 idempotent persistence, REQ-02 conflict refusal),
driving the real CLI through CliRunner against the tmp_config vault."""

from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.config import load_vault_config, parse_tag_vocabulary


def _vocab(vault):
    return parse_tag_vocabulary(load_vault_config(vault))


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
