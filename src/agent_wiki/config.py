from pathlib import Path
import os
import yaml
import click


def get_config_dir() -> Path:
    """Return the config directory. Respects AGENT_WIKI_CONFIG_DIR env var."""
    env = os.environ.get("AGENT_WIKI_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "agent-wiki"


def load_user_config() -> dict:
    """Load user config from config dir. Returns empty dict if not found."""
    config_file = get_config_dir() / "config.yaml"
    if not config_file.exists():
        return {}
    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def save_user_config(config: dict) -> None:
    """Save user config to config dir. Creates directory if needed."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def load_vault_config(vault_path: Path) -> dict:
    """Load wiki.yaml from a vault directory."""
    config_file = vault_path / "wiki.yaml"
    if not config_file.exists():
        raise FileNotFoundError(f"No wiki.yaml found in {vault_path}")
    with open(config_file) as f:
        return yaml.safe_load(f)


def get_vault_path() -> Path:
    """Get the vault path from user config. Raises if not configured."""
    config = load_user_config()
    vault_path = config.get("vault_path")
    if not vault_path:
        raise click.UsageError(
            "No vault configured. Run 'awiki init <path>' first."
        )
    path = Path(vault_path).expanduser()
    if not path.exists():
        raise click.UsageError(f"Vault not found at {path}")
    return path


def auto_context_enabled(vault_path: Path) -> bool:
    """Return True if the auto-context hook should fire for this vault.

    Resolution order:
      1. AWIKI_AUTO_CONTEXT env var (accepts 1/0, true/false, yes/no, on/off)
      2. wiki.yaml `auto_context` key (default True when key present but unset)
      3. False if no wiki.yaml exists (vault not initialized)
    """
    env = os.environ.get("AWIKI_AUTO_CONTEXT")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        config = load_vault_config(vault_path)
    except FileNotFoundError:
        return False
    return bool(config.get("auto_context", True))
