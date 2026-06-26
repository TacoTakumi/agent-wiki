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


def resolve_vault_override() -> "Path | None":
    """Return an explicit vault override, or None.

    Precedence: the `--vault` group option (read from the active click context)
    beats the `AWIKI_VAULT` env var; both beat the configured vault. The override
    is not validated here — callers decide how to report a missing path."""
    flag = None
    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        # The --vault option lives on the top-level group; its value sits on the
        # root context's params regardless of which subcommand is running.
        flag = ctx.find_root().params.get("vault")
    raw = flag or os.environ.get("AWIKI_VAULT")
    if not raw:
        return None
    return Path(raw).expanduser()


def _override_path_or_raise() -> "Path | None":
    """Resolve and validate a vault override. Raises a friendly UsageError when an
    override is set but points nowhere; returns None when no override is set."""
    override = resolve_vault_override()
    if override is None:
        return None
    if not override.exists():
        raise click.UsageError(
            f"Vault override points at {override}, which does not exist "
            f"(set via --vault or AWIKI_VAULT)."
        )
    return override


def _stale_vault_error(path: Path) -> click.UsageError:
    """A 'Vault not found' error that names the offending config file and key and
    points at the override escape hatch."""
    config_file = get_config_dir() / "config.yaml"
    return click.UsageError(
        f"Vault not found at {path} — vault_path in {config_file} points there. "
        f"Edit it, pass --vault PATH, or set AWIKI_VAULT."
    )


def get_vault_path() -> Path:
    """Get the vault path: override (--vault/AWIKI_VAULT) first, then user config."""
    override = _override_path_or_raise()
    if override is not None:
        return override
    config = load_user_config()
    vault_path = config.get("vault_path")
    if not vault_path:
        raise click.UsageError(
            "No vault configured. Run 'awiki init <path>' first."
        )
    path = Path(vault_path).expanduser()
    if not path.exists():
        raise _stale_vault_error(path)
    return path


def get_backend():
    """Resolve the vault into a VaultService. An explicit override forces a local
    vault; otherwise a configured remote server wins, then the local vault_path."""
    override = _override_path_or_raise()
    if override is not None:
        from agent_wiki.service import LocalVaultService
        return LocalVaultService(override)
    config = load_user_config()
    server = config.get("server")
    if server and server.get("url"):
        from agent_wiki.remote import RemoteVaultService
        return RemoteVaultService(server["url"], server.get("token"))
    vault_path = config.get("vault_path")
    if vault_path:
        from agent_wiki.service import LocalVaultService
        path = Path(vault_path).expanduser()
        if not path.exists():
            raise _stale_vault_error(path)
        return LocalVaultService(path)
    raise click.UsageError("No vault configured. Run 'awiki init <path>' first.")


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
