from pathlib import Path
from dataclasses import dataclass, field
import os
import yaml
import click


TAG_MODES = ("off", "warn", "strict")


@dataclass(frozen=True)
class TagVocabulary:
    """A parsed wiki.yaml 'tags:' block: a mode plus a preferred→aliases mapping.

    `mode` is one of off|warn|strict. `vocabulary` maps each preferred tag to its
    list of aliases. An absent block resolves to mode 'off' with an empty mapping
    (the 'no vocabulary configured' value)."""

    mode: str
    vocabulary: dict = field(default_factory=dict)

    @property
    def is_off(self) -> bool:
        """True when canonicalization should not run: mode off or no vocabulary."""
        return self.mode == "off" or not self.vocabulary


@dataclass(frozen=True)
class VocabularyConflict:
    """One ambiguous token claimed by more than one preferred term — either an
    alias bound to two preferred terms, or a string that is both a preferred term
    and an alias. `token` is the lowercased offender; `preferred` lists the
    preferred terms that claim it (in their configured casing)."""

    token: str
    preferred: tuple

    @property
    def message(self) -> str:
        terms = ", ".join(self.preferred)
        return (
            f"tag '{self.token}' is claimed by multiple preferred terms: {terms}"
        )


def parse_tag_vocabulary(config) -> TagVocabulary:
    """Parse a vault config dict's 'tags:' block into a TagVocabulary.

    An absent (or empty) block yields the off/empty value without error. A block
    present with no 'mode' defaults to 'warn' (the block exists to be enforced).
    Alias lists are coerced: a missing/None list becomes [], a scalar becomes a
    one-element list; keys and aliases are stringified. An unrecognized mode is a
    configuration error (ValueError)."""
    block = (config or {}).get("tags")
    if not block:
        return TagVocabulary(mode="off", vocabulary={})

    mode = block.get("mode")
    # A bare 'mode: off' is coerced to boolean False by the YAML 1.1 reader
    # (PyYAML); map it back to the 'off' mode so a hand-written or round-tripped
    # 'mode: off' is read correctly instead of failing as the string 'false'.
    if mode is False:
        mode = "off"
    mode = "warn" if mode is None else str(mode).strip().lower()
    if mode not in TAG_MODES:
        raise ValueError(
            f"invalid tags mode {mode!r} in wiki.yaml; expected one of "
            f"{', '.join(TAG_MODES)}"
        )

    raw_vocab = block.get("vocabulary") or {}
    vocabulary = {}
    for preferred, aliases in raw_vocab.items():
        if aliases is None:
            alias_list = []
        elif isinstance(aliases, (list, tuple)):
            alias_list = [str(a) for a in aliases]
        else:
            alias_list = [str(aliases)]
        vocabulary[str(preferred)] = alias_list

    return TagVocabulary(mode=mode, vocabulary=vocabulary)


def load_tag_vocabulary(vault_path: Path) -> TagVocabulary:
    """Read the tag vocabulary from a vault's wiki.yaml via load_vault_config."""
    return parse_tag_vocabulary(load_vault_config(vault_path))


def detect_vocabulary_conflicts(vocab: TagVocabulary) -> list:
    """Return the vocabulary's conflicts (empty list when clean).

    A token conflicts when more than one preferred term claims it. Each preferred
    term claims its own lowercased form and each of its lowercased aliases; a
    token claimed by two or more distinct preferred terms is ambiguous to
    canonicalize. Matching is case-insensitive, mirroring canonicalization."""
    claimants: dict[str, list[str]] = {}
    for preferred, aliases in vocab.vocabulary.items():
        for token in [preferred, *aliases]:
            key = str(token).strip().lower()
            owners = claimants.setdefault(key, [])
            if preferred not in owners:
                owners.append(preferred)

    return [
        VocabularyConflict(token=token, preferred=tuple(owners))
        for token, owners in claimants.items()
        if len(owners) > 1
    ]


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
