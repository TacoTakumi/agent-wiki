from click.testing import CliRunner
from agent_wiki.cli import cli


def test_token_add_prints_secret_once(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    res = CliRunner().invoke(cli, ["token", "add", "laptop", "--role", "admin"])
    assert res.exit_code == 0
    assert "laptop" in res.output
    res2 = CliRunner().invoke(cli, ["token", "list"])
    assert "laptop" in res2.output and "admin" in res2.output


def test_token_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    r = CliRunner()
    r.invoke(cli, ["token", "add", "x", "--role", "reader"])
    res = r.invoke(cli, ["token", "revoke", "x"])
    assert res.exit_code == 0
    assert "x" not in r.invoke(cli, ["token", "list"]).output
