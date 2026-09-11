"""The awiki vault command group (registry management)."""

import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from conftest import make_vault


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))
    return path


def _setup_untrusted_local(tmp_path, monkeypatch):
    """A global config with one vault plus an untrusted local config declaring
    another; returns (global config file, project dir, local vault path)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(
        config_dir / "config.yaml",
        {"vaults": {"main": {"path": str(g_main)}}},
    )
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    proj = home / "proj"
    _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    monkeypatch.chdir(proj)
    return config_file, proj, personal


def test_vault_trust_records_dir_and_honors_local_config(tmp_path, monkeypatch):
    from agent_wiki.config import load_registry

    config_file, proj, personal = _setup_untrusted_local(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, ["vault", "trust", str(proj)])
    assert result.exit_code == 0, result.output

    persisted = yaml.safe_load(config_file.read_text())
    assert str(proj.resolve()) in persisted["trusted_dirs"]

    registry = load_registry()
    assert set(registry) == {"main", "personal"}
    assert registry["personal"].path == personal

    status = CliRunner().invoke(cli, ["status"])
    assert status.exit_code == 0, status.output
    assert "vault trust" not in status.stderr


def test_vault_trust_is_idempotent(tmp_path, monkeypatch):
    config_file, proj, _personal = _setup_untrusted_local(tmp_path, monkeypatch)

    runner = CliRunner()
    assert runner.invoke(cli, ["vault", "trust", str(proj)]).exit_code == 0
    assert runner.invoke(cli, ["vault", "trust", str(proj)]).exit_code == 0

    persisted = yaml.safe_load(config_file.read_text())
    assert persisted["trusted_dirs"].count(str(proj.resolve())) == 1


def test_vault_trust_missing_dir_errors(tmp_path, monkeypatch):
    _setup_untrusted_local(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, ["vault", "trust", str(tmp_path / "ghost")])
    assert result.exit_code != 0


def test_bare_init_after_vault_trust_preserves_config_keys(tmp_path, monkeypatch):
    """A bare init's legacy vault_path write merges over the existing
    config — trusted_dirs (from vault trust) and any server key survive; only
    vault_path changes."""
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(
        config_dir / "config.yaml",
        {"server": {"url": "http://x:8731", "token": "tok"}},
    )
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    proj = tmp_path / "proj"
    proj.mkdir()

    runner = CliRunner()
    trusted = runner.invoke(cli, ["vault", "trust", str(proj)])
    assert trusted.exit_code == 0, trusted.output

    inited = runner.invoke(cli, ["init", str(tmp_path / "new-vault")])
    assert inited.exit_code == 0, inited.output

    persisted = yaml.safe_load(config_file.read_text())
    assert persisted["vault_path"] == str(tmp_path / "new-vault")
    assert persisted["trusted_dirs"] == [str(proj.resolve())]
    assert persisted["server"] == {"url": "http://x:8731", "token": "tok"}
    assert "vaults" not in persisted  # the write stays in legacy form


# --- vault add + lazy schema migration -----------------------------------------

def _legacy_config(tmp_path, monkeypatch):
    """A legacy vault_path config; returns (config_file, legacy_vault)."""
    legacy = make_vault(tmp_path / "legacy-vault")
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(
        config_dir / "config.yaml", {"vault_path": str(legacy)})
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return config_file, legacy


def test_vault_add_migrates_legacy_config_to_vaults_schema(tmp_path, monkeypatch):
    config_file, legacy = _legacy_config(tmp_path, monkeypatch)
    second = make_vault(tmp_path / "second-vault")

    result = CliRunner().invoke(cli, ["vault", "add", "second", str(second)])
    assert result.exit_code == 0, result.output

    persisted = yaml.safe_load(config_file.read_text())
    assert "vault_path" not in persisted
    assert "server" not in persisted
    assert persisted["vaults"]["main"] == {"path": str(legacy)}
    assert persisted["vaults"]["second"] == {"path": str(second)}

    listed = CliRunner().invoke(cli, ["vault", "list"])
    assert listed.exit_code == 0, listed.output
    assert "main" in listed.output
    assert "second" in listed.output


def test_migration_keeps_both_vault_path_and_server_on_main(tmp_path):
    """A legacy config holding vault_path AND server
    migrates to a main entry preserving both keys, and that entry parses to
    the same VaultEntry the legacy synthesis produces — url wins at backend
    selection, the path stays for local resolution (serve/tag/doctor)."""
    from agent_wiki.config import migrate_to_vaults_schema
    from agent_wiki.registry import parse_registry

    legacy = {
        "vault_path": str(tmp_path / "v"),
        "server": {"url": "https://wiki.example.com", "token": "tok"},
    }
    migrated = migrate_to_vaults_schema(legacy)
    assert "vault_path" not in migrated
    assert "server" not in migrated
    assert migrated["vaults"]["main"] == {
        "url": "https://wiki.example.com",
        "token": "tok",
        "path": str(tmp_path / "v"),
    }

    main = parse_registry(migrated)["main"]
    assert str(main.path) == str(tmp_path / "v")
    assert main.url == "https://wiki.example.com"
    assert main.token == "tok"
    assert main.is_remote


def test_vault_add_remote_url_with_token(tmp_path, monkeypatch):
    config_file, _legacy = _legacy_config(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli, ["vault", "add", "team", "http://127.0.0.1:9", "--token", "tok"])
    assert result.exit_code == 0, result.output

    persisted = yaml.safe_load(config_file.read_text())
    assert persisted["vaults"]["team"] == {
        "url": "http://127.0.0.1:9", "token": "tok"}


def test_vault_add_rejects_illegal_name(tmp_path, monkeypatch):
    config_file, _legacy = _legacy_config(tmp_path, monkeypatch)
    second = make_vault(tmp_path / "second-vault")
    before = config_file.read_bytes()

    for bad in ("Bad Name", "UPPER", "with/slash", ""):
        result = CliRunner().invoke(cli, ["vault", "add", bad, str(second)])
        assert result.exit_code != 0, f"accepted illegal name {bad!r}"
    assert config_file.read_bytes() == before


def test_vault_add_rejects_path_without_wiki_yaml(tmp_path, monkeypatch):
    config_file, _legacy = _legacy_config(tmp_path, monkeypatch)
    bare = tmp_path / "not-a-vault"
    bare.mkdir()
    before = config_file.read_bytes()

    result = CliRunner().invoke(cli, ["vault", "add", "bare", str(bare)])
    assert result.exit_code != 0
    assert "wiki.yaml" in result.output + result.stderr
    assert config_file.read_bytes() == before


def test_vault_add_rejects_malformed_url(tmp_path, monkeypatch):
    config_file, _legacy = _legacy_config(tmp_path, monkeypatch)
    before = config_file.read_bytes()

    for bad in ("http://", "ftp://host", "http://host/some/path"):
        result = CliRunner().invoke(cli, ["vault", "add", "team", bad])
        assert result.exit_code != 0, f"accepted malformed url {bad!r}"
    assert config_file.read_bytes() == before


def test_vault_add_rejects_duplicate_name(tmp_path, monkeypatch):
    config_file, _legacy = _legacy_config(tmp_path, monkeypatch)
    second = make_vault(tmp_path / "second-vault")
    assert CliRunner().invoke(
        cli, ["vault", "add", "second", str(second)]).exit_code == 0
    before = config_file.read_bytes()

    result = CliRunner().invoke(cli, ["vault", "add", "second", str(second)])
    assert result.exit_code != 0
    assert config_file.read_bytes() == before


# --- vault list: read-only registry view ---------------------------------------

def test_vault_list_shows_both_rows_and_writes_nothing(tmp_path, monkeypatch):
    work = make_vault(tmp_path / "work-vault")
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(config_dir / "config.yaml", {
        "vaults": {
            "main": {"path": str(work)},
            "team": {"url": "http://127.0.0.1:9", "token": "t"},
        },
    })
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    before = config_file.read_bytes()

    result = CliRunner().invoke(cli, ["vault", "list"])
    assert result.exit_code == 0, result.output

    lines = result.output.splitlines()
    main_row = next(line for line in lines if " main " in f" {line} ")
    team_row = next(line for line in lines if " team " in f" {line} ")

    assert "local" in main_row
    assert str(work) in main_row
    assert "ok" in main_row
    assert main_row.lstrip().startswith("*")  # the default marker

    assert "remote" in team_row
    assert "http://127.0.0.1:9" in team_row
    assert "unreachable" in team_row
    assert not team_row.lstrip().startswith("*")

    # Declaring config is named on both rows; nothing was written.
    assert str(config_file) in main_row
    assert str(config_file) in team_row
    assert config_file.read_bytes() == before


def test_vault_list_names_declaring_local_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    g_main = make_vault(tmp_path / "g-main")
    personal = make_vault(tmp_path / "personal")
    proj = home / "proj"
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(config_dir / "config.yaml", {
        "vaults": {"main": {"path": str(g_main)}},
        "trusted_dirs": [str(proj)],
    })
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    local_file = _write_yaml(
        proj / ".agent-wiki" / "config.yaml",
        {"vaults": {"personal": {"path": str(personal)}}},
    )
    monkeypatch.chdir(proj)

    result = CliRunner().invoke(cli, ["vault", "list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    personal_row = next(line for line in lines if "personal" in line)
    main_row = next(line for line in lines if " main " in f" {line} ")
    assert str(local_file) in personal_row
    assert str(config_file) in main_row


def test_vault_trust_keeps_legacy_config_keys(tmp_path, monkeypatch):
    """Trusting must not rewrite a legacy config into the vaults: schema."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    vault_dir = make_vault(tmp_path / "legacy-vault")
    config_dir = tmp_path / "global-config"
    config_file = _write_yaml(
        config_dir / "config.yaml", {"vault_path": str(vault_dir)}
    )
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    proj = home / "proj"
    proj.mkdir()

    result = CliRunner().invoke(cli, ["vault", "trust", str(proj)])
    assert result.exit_code == 0, result.output

    persisted = yaml.safe_load(config_file.read_text())
    assert persisted["vault_path"] == str(vault_dir)
    assert "vaults" not in persisted
    assert str(proj.resolve()) in persisted["trusted_dirs"]


# --- absence guard: unconfigured vaults are invisible --------------------------

def test_unconfigured_vault_is_invisible_everywhere(tmp_path, monkeypatch):
    """A vault directory absent from the merged config view is unreachable:
    search, show, the hook, and vault list never reference it."""
    import json

    work = make_vault(tmp_path / "work-vault")
    # The personal vault exists on disk with content - but is not configured.
    personal = make_vault(tmp_path / "personal-vault")
    (personal / "research" / "zeb.md").write_text(
        "---\ntitle: Zeb\ntopic: research\n---\n\n# Zeb\n\n"
        "zebras migrate seasonally across the plains\n")
    config_dir = tmp_path / "cfg"
    config_file = _write_yaml(
        config_dir / "config.yaml", {"vaults": {"work": {"path": str(work)}}})
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    runner = CliRunner()

    searched = runner.invoke(cli, ["search", "zebras"])
    assert searched.exit_code == 0, searched.output
    assert "No results found." in searched.output
    assert "personal" not in searched.output

    shown = runner.invoke(cli, ["show", "research/zeb.md"])
    assert shown.exit_code != 0
    assert "personal" not in shown.output

    hook = runner.invoke(
        cli, ["context", "--output-format", "plain"],
        input=json.dumps(
            {"prompt": "tell me how zebras migrate seasonally across plains"}))
    assert hook.exit_code == 0
    assert "personal" not in hook.output

    listed = runner.invoke(cli, ["vault", "list"])
    assert listed.exit_code == 0, listed.output
    assert "personal" not in listed.output
    assert str(config_file) in listed.output
