from pathlib import Path
import yaml
from agent_wiki.config import save_user_config

DEFAULT_TOPICS = ["projects", "decisions", "research", "tools"]


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
    }
    (vault_path / "wiki.yaml").write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    (vault_path / "raw").mkdir(exist_ok=True)

    for topic in DEFAULT_TOPICS:
        (vault_path / topic).mkdir(exist_ok=True)

    (vault_path / "index.md").write_text("# Index\n\n*Run `awiki index` to rebuild.*\n")
    (vault_path / "log.md").write_text("# Activity Log\n\n")

    save_user_config({"vault_path": str(vault_path)})
