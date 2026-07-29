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


def test_init_remote_preserves_registry_and_trust(tmp_path, monkeypatch):
    """T-23: init --remote against a config holding vaults: and trusted_dirs
    preserves every entry and the allowlist — the remote lands as a vaults:
    entry via the REQ-24 migration, never a config wipe."""
    cd = tmp_path / "config"
    _write_user_config(cd, {
        "vaults": {"work": {"path": str(tmp_path / "work")}},
        "trusted_dirs": [str(tmp_path / "proj")],
    })
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(
        cli, ["init", "--remote", "http://x:8731", "--token", "tok"])
    assert res.exit_code == 0, res.output
    data = yaml.safe_load((cd / "config.yaml").read_text())
    assert data["vaults"]["work"] == {"path": str(tmp_path / "work")}
    assert data["vaults"]["main"] == {"url": "http://x:8731", "token": "tok"}
    assert data["trusted_dirs"] == [str(tmp_path / "proj")]
    assert "server" not in data


def test_init_remote_with_name_registers_named_entry(tmp_path, monkeypatch):
    """T-23: a named remote init is a needs-more write (REQ-24) and lands in
    the vaults: schema even from an empty config."""
    cd = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(
        cli, ["init", "--remote", "http://x:8731", "--token", "tok",
              "--name", "team"])
    assert res.exit_code == 0, res.output
    data = yaml.safe_load((cd / "config.yaml").read_text())
    assert data["vaults"]["team"] == {"url": "http://x:8731", "token": "tok"}
    assert "server" not in data


def test_init_remote_on_trusted_legacy_config_keeps_local_main(
    tmp_path, monkeypatch
):
    """T-23: a legacy vault_path config with trusted_dirs migrates on remote
    init; the url/token merge onto main beside its path (T-22 hybrid form)."""
    cd = tmp_path / "config"
    _write_user_config(cd, {
        "vault_path": str(tmp_path / "v"),
        "trusted_dirs": [str(tmp_path / "proj")],
    })
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(
        cli, ["init", "--remote", "http://x:8731", "--token", "tok"])
    assert res.exit_code == 0, res.output
    data = yaml.safe_load((cd / "config.yaml").read_text())
    assert data["vaults"]["main"] == {
        "path": str(tmp_path / "v"),
        "url": "http://x:8731",
        "token": "tok",
    }
    assert data["trusted_dirs"] == [str(tmp_path / "proj")]
    assert "vault_path" not in data
    assert "server" not in data


def test_init_remote_on_vault_path_only_config_keeps_local_path(
    tmp_path, monkeypatch
):
    """T-30: init --remote against a config holding only vault_path must not
    drop the local vault — it migrates to a hybrid main entry carrying the
    path beside the new url/token (the last destructive init path)."""
    cd = tmp_path / "config"
    _write_user_config(cd, {"vault_path": str(tmp_path / "v")})
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(
        cli, ["init", "--remote", "http://x:8731", "--token", "tok"])
    assert res.exit_code == 0, res.output
    data = yaml.safe_load((cd / "config.yaml").read_text())
    assert data["vaults"]["main"] == {
        "path": str(tmp_path / "v"),
        "url": "http://x:8731",
        "token": "tok",
    }
    assert "vault_path" not in data
    assert "server" not in data


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


def test_init_help_shows_remote_url_shape():
    # The --remote help text must show the expected URL shape (scheme + port),
    # not just "server URL", so users know it isn't a bare host:port.
    res = CliRunner().invoke(cli, ["init", "--help"])
    assert res.exit_code == 0
    assert "http://host:8731" in res.output


def test_init_remote_prompt_shows_url_example(tmp_path, monkeypatch):
    # The interactive "Server URL" prompt must include an example so the user
    # knows to enter a full base URL with scheme and port.
    cd = tmp_path / "config"
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(cd))
    res = CliRunner().invoke(cli, ["init"], input="r\nhttp://myhost:8731\nsecret\n")
    assert res.exit_code == 0
    assert "Server URL (e.g. http://host:8731)" in res.output


# --- no default-vault mutator exists (T-20, REQ-04) ----------------------------

def test_awiki_use_is_an_unknown_command():
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    result = CliRunner().invoke(cli, ["use", "work"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_no_source_write_path_for_default_vault_key():
    """Source scan: no subscript assignment or dict-literal construction of
    the default_vault key anywhere in the package - the key is repointed by
    hand-editing config only."""
    import re
    from pathlib import Path
    import agent_wiki

    src_root = Path(agent_wiki.__file__).parent
    offenders = []
    for path in src_root.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\[.default_vault.\]\s*=", line) or \
               re.search(r"[\"']default_vault[\"']\s*:", line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []
