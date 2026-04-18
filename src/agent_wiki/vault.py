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


def init_vault(vault_path: Path) -> None:
    """Initialize a new wiki vault at the given path."""
    vault_path = vault_path.resolve()

    if (vault_path / "wiki.yaml").exists():
        raise FileExistsError(f"Vault already exists at {vault_path}")

    vault_path.mkdir(parents=True, exist_ok=True)

    config = {
        "vault": {"name": vault_path.name, "version": 1},
        "topics": DEFAULT_TOPICS,
        "default_topic": "research",
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

    save_user_config({"vault_path": str(vault_path)})
