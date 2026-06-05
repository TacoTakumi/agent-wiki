import yaml
from click.testing import CliRunner
from agent_wiki.cli import cli
from agent_wiki.config import get_backend


def _write_user_config(config_dir, data):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.dump(data))


def test_get_backend_remote(tmp_path, monkeypatch):
    cd = tmp_path / "config"
    _write_user_config(cd, {"server": {"url": "http://x:8731", "token": "t"}})
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    from agent_wiki.remote import RemoteVaultService
    assert isinstance(get_backend(), RemoteVaultService)


def test_get_backend_local(tmp_config):
    from agent_wiki.service import LocalVaultService
    assert isinstance(get_backend(), LocalVaultService)


def test_init_remote_writes_server_block(tmp_path, monkeypatch):
    cd = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(cli, ["init", "--remote", "http://x:8731", "--token", "tok"])
    assert res.exit_code == 0
    data = yaml.safe_load((cd / "config.yaml").read_text())
    assert data["server"] == {"url": "http://x:8731", "token": "tok"}


def test_init_clear_removes_server(tmp_path, monkeypatch):
    cd = tmp_path / "config"
    _write_user_config(cd, {"server": {"url": "http://x", "token": "t"}})
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(cli, ["init", "--clear"])
    assert res.exit_code == 0
    assert "server" not in yaml.safe_load((cd / "config.yaml").read_text())


def test_init_local_still_works(tmp_path, monkeypatch):
    cd = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    target = tmp_path / "v"
    res = CliRunner().invoke(cli, ["init", str(target)])
    assert res.exit_code == 0
    assert (target / "wiki.yaml").exists()
