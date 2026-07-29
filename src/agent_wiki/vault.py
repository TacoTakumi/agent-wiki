from pathlib import Path
import yaml
from agent_wiki.config import save_user_config

DEFAULT_TOPICS = ["projects", "decisions", "research", "tools", "sessions"]


def _default_sources_config() -> dict:
    return {
        "claude_code": {"enabled": True, "path": "~/.claude/projects"},
        "opencode": {
            "enabled": True,
            "db_path": "~/.local/share/opencode/opencode.db",
        },
        "drop_zone": {"enabled": True, "path": "incoming"},
    }


def init_vault(vault_path: Path, name: str | None = None) -> None:
    """Initialize a new wiki vault at the given path.

    With ``name``, the vault registers under that name in the config's
    vaults: schema (migrating a legacy config). Bare init keeps today's
    behavior: a legacy ``vault_path`` write — unless the config already
    carries a vaults: block, in which case it registers as ``main`` there
    instead of clobbering the registry."""
    vault_path = vault_path.resolve()

    if (vault_path / "wiki.yaml").exists():
        raise FileExistsError(f"Vault already exists at {vault_path}")

    vault_path.mkdir(parents=True, exist_ok=True)

    config = {
        "vault": {"name": vault_path.name, "version": 1},
        "topics": DEFAULT_TOPICS,
        "default_topic": "research",
        "auto_context": True,
        "conversations": {
            "topic": "sessions",
            "include_live": False,
        },
        "summarizer": {"type": "none"},
        "sources": _default_sources_config(),
    }
    (vault_path / "wiki.yaml").write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    (vault_path / "raw").mkdir(exist_ok=True)
    (vault_path / "raw" / "sessions").mkdir(exist_ok=True)
    (vault_path / "incoming").mkdir(exist_ok=True)

    for topic in DEFAULT_TOPICS:
        (vault_path / topic).mkdir(exist_ok=True)

    (vault_path / "index.md").write_text("# Index\n\n*Run `awiki index` to rebuild.*\n")
    (vault_path / "log.md").write_text("# Activity Log\n\n")

    _register_vault(vault_path, name)


def _register_vault(vault_path: Path, name: str | None) -> None:
    from agent_wiki.config import load_user_config, migrate_to_vaults_schema

    config = load_user_config()
    if name is None and not config.get("vaults"):
        # Legacy form (REQ-24), merged over the existing config: only
        # vault_path changes — trusted_dirs and server keys survive.
        config["vault_path"] = str(vault_path)
        save_user_config(config)
        return
    config = migrate_to_vaults_schema(config)
    config.setdefault("vaults", {})[name or "main"] = {
        "path": str(vault_path)}
    save_user_config(config)
