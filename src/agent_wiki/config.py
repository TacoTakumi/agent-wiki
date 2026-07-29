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


def migrate_to_vaults_schema(config: dict) -> dict:
    """Return a copy of a user-config dict in the vaults: schema (REQ-24).

    A config already carrying a vaults: block is returned unchanged (copied).
    Legacy keys synthesize the 'main' entry and are dropped: a server url wins
    over vault_path when both are present (matching resolution precedence).
    Pure — writes nothing."""
    config = dict(config)
    if config.get("vaults"):
        return config
    vault_path = config.pop("vault_path", None)
    server = config.pop("server", None) or {}
    entry = {}
    if server.get("url"):
        entry["url"] = str(server["url"])
        if server.get("token") is not None:
            entry["token"] = str(server["token"])
    elif vault_path:
        entry["path"] = str(vault_path)
    if entry:
        config["vaults"] = {"main": entry}
    return config


def discover_local_config(cwd: "Path | None" = None) -> "Path | None":
    """Return the nearest .agent-wiki/config.yaml walking up from cwd to $HOME
    inclusive, or None. Only the nearest one counts; outside $HOME's tree there
    is no discovery."""
    cur = (Path(cwd) if cwd is not None else Path.cwd()).resolve()
    home = Path.home().resolve()
    if cur != home and home not in cur.parents:
        return None
    for directory in (cur, *cur.parents):
        candidate = directory / ".agent-wiki" / "config.yaml"
        if candidate.is_file():
            return candidate
        if directory == home:
            break
    return None


# Local-config files already noticed as untrusted this process, so a command
# that resolves the registry more than once emits the notice exactly once.
_untrusted_noticed: set = set()


def _local_config_if_trusted(global_config: dict) -> "tuple | None":
    """Return (path, dict) for the discovered local config when its directory
    is on the global trust allowlist (trusted_dirs). An untrusted one is
    ignored with a single stderr notice naming the file and the trust
    command."""
    local_file = discover_local_config()
    if local_file is None:
        return None
    project_dir = local_file.parent.parent
    trusted = {
        str(Path(t).expanduser().resolve())
        for t in (global_config.get("trusted_dirs") or [])
    }
    if str(project_dir.resolve()) not in trusted:
        if str(local_file) not in _untrusted_noticed:
            _untrusted_noticed.add(str(local_file))
            click.echo(
                f"Ignoring untrusted local config {local_file}; run "
                f"'awiki vault trust {project_dir}' to trust it.",
                err=True,
            )
        return None
    with open(local_file) as f:
        return local_file, (yaml.safe_load(f) or {})


def load_effective_config() -> tuple:
    """Return (registry, default_vault) — the global registry with the nearest
    trusted local config merged additively over it, local winning on vault-name
    collision and its default_vault beating the global one. Read-only."""
    from dataclasses import replace
    from agent_wiki.registry import parse_registry
    global_config = load_user_config()
    global_file = str(get_config_dir() / "config.yaml")
    registry = {
        name: replace(entry, origin=global_file)
        for name, entry in parse_registry(global_config).items()
    }
    default_vault = global_config.get("default_vault")
    local = _local_config_if_trusted(global_config)
    if local is not None:
        local_file, local_config = local
        # A relative path: is anchored to the directory containing .agent-wiki,
        # not the process cwd — the config means the same vault from any subdir.
        base = local_file.parent.parent
        local_registry = {
            name: replace(
                entry,
                origin=str(local_file),
                path=(
                    entry.path
                    if entry.path is None or entry.path.is_absolute()
                    else base / entry.path
                ),
            )
            for name, entry in parse_registry(local_config).items()
        }
        registry = {**registry, **local_registry}
        if local_config.get("default_vault"):
            default_vault = local_config["default_vault"]
    return registry, default_vault


def load_registry() -> dict:
    """The named vault registry ({name: VaultEntry}): the global config with
    the nearest trusted local config merged over it. Read-only — never writes
    a config file."""
    registry, _default_vault = load_effective_config()
    return registry


def load_vault_config(vault_path: Path) -> dict:
    """Load wiki.yaml from a vault directory."""
    config_file = vault_path / "wiki.yaml"
    if not config_file.exists():
        raise FileNotFoundError(f"No wiki.yaml found in {vault_path}")
    with open(config_file) as f:
        return yaml.safe_load(f)


def _raw_vault_override() -> "str | None":
    """Return the raw override value (--vault flag or AWIKI_VAULT), or None.

    Precedence: the `--vault` group option (read from the active click context)
    beats the `AWIKI_VAULT` env var; both beat the configured vaults."""
    flag = None
    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        # The --vault option lives on the top-level group; its value sits on the
        # root context's params regardless of which subcommand is running.
        flag = ctx.find_root().params.get("vault")
    return flag or os.environ.get("AWIKI_VAULT") or None


def _name_eligible(value: str) -> bool:
    """True when an override value may be looked up as a configured vault name.

    A ./ or ../ prefix, a ~ prefix, an absolute path, or any path separator
    forces path interpretation."""
    return (
        not os.path.isabs(value)
        and not value.startswith((".", "~"))
        and os.sep not in value
    )


def resolve_vault_override():
    """Return the vault override as a VaultEntry, or None.

    A bare value matching a configured vault name narrows to that entry (local
    or remote); any other value is a config-free local vault at that path. The
    entry is not validated here — callers decide how to report a missing path."""
    from agent_wiki.registry import VaultEntry
    raw = _raw_vault_override()
    if raw is None:
        return None
    if _name_eligible(raw):
        entry = load_registry().get(raw)
        if entry is not None:
            return entry
    return VaultEntry(name=raw, path=Path(raw).expanduser())


def _override_entry_or_raise():
    """Resolve and validate a vault override. Raises a friendly UsageError when
    an override's local path points nowhere; returns None when no override is
    set. Remote (url) entries carry no path to validate."""
    entry = resolve_vault_override()
    if entry is None:
        return None
    if entry.path is not None and not entry.path.exists():
        raise click.UsageError(
            f"Vault override points at {entry.path}, which does not exist "
            f"(set via --vault or AWIKI_VAULT)."
        )
    return entry


def _stale_vault_error(path: Path) -> click.UsageError:
    """A 'Vault not found' error that names the offending config file and key and
    points at the override escape hatch."""
    config_file = get_config_dir() / "config.yaml"
    return click.UsageError(
        f"Vault not found at {path} — vault_path in {config_file} points there. "
        f"Edit it, pass --vault PATH, or set AWIKI_VAULT."
    )


def _default_entry():
    """Resolve the default vault entry from the merged (global + trusted
    local) registry view."""
    from agent_wiki.registry import resolve_default_vault
    registry, default_vault = load_effective_config()
    return resolve_default_vault(registry, default_vault)


def get_vault_path() -> Path:
    """Get the vault path: override (--vault/AWIKI_VAULT) first, then the
    registry's default vault (which a legacy vault_path config synthesizes)."""
    override = _override_entry_or_raise()
    if override is not None:
        if override.path is None:
            raise click.UsageError(
                f"Vault '{override.name}' is remote (url); this operation "
                f"needs a local vault."
            )
        return override.path
    entry = _default_entry()
    if entry.path is None:
        raise click.UsageError(
            "No vault configured. Run 'awiki init <path>' first."
        )
    if not entry.path.exists():
        raise _stale_vault_error(entry.path)
    return entry.path


def backend_for_entry(entry):
    """Build a VaultService for a registry entry: a url entry is remote, a path
    entry local. A legacy-synthesized entry may carry both; url wins, matching
    the pre-registry server-over-vault_path precedence."""
    if entry.url:
        from agent_wiki.remote import RemoteVaultService
        return RemoteVaultService(entry.url, entry.token)
    from agent_wiki.service import LocalVaultService
    if not entry.path.exists():
        raise _stale_vault_error(entry.path)
    return LocalVaultService(entry.path)


def get_backend():
    """Resolve the vault into a VaultService. An explicit override wins — a
    name-matched entry may be remote; a path override is always local — then
    the registry's default entry decides."""
    override = _override_entry_or_raise()
    if override is not None:
        return backend_for_entry(override)
    return backend_for_entry(_default_entry())


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
