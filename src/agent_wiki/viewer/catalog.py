"""The viewer's catalog: which vaults are served.

`build_vault_catalog` is pure: it decides from a parsed registry and an
optional override entry alone, so tests drive it without any config.
`load_vault_catalog` feeds it the real registry and override readers from
`agent_wiki.config`.
"""

from dataclasses import dataclass, field


# The name a bare-path override is served under: it has no registry name.
OVERRIDE_PATH_NAME = "local"


@dataclass(frozen=True)
class VaultCatalog:
    """The served vaults ({name: path}, sorted by name), the number of remote
    entries skipped (the viewer reads local files only), and the names of
    local vaults whose directory does not exist."""

    vaults: dict = field(default_factory=dict)
    skipped_remote: int = 0
    unavailable: tuple = ()


def build_vault_catalog(registry: dict, override=None) -> VaultCatalog:
    """Build the catalog from a registry ({name: VaultEntry}) and an override.

    With no override every registry entry is considered. An override narrows
    to that one entry: a name-matched override (the registry's own entry) keeps
    its registry name, a path override is served as 'local'. An entry with a
    url is remote and skipped, even with a path beside it (url wins, as at
    backend selection); a local entry whose path is missing is unavailable."""
    if override is None:
        entries = [registry[name] for name in sorted(registry)]
        names = [entry.name for entry in entries]
    elif registry.get(override.name) == override:
        entries, names = [override], [override.name]
    else:
        entries, names = [override], [OVERRIDE_PATH_NAME]

    vaults = {}
    skipped_remote = 0
    unavailable = []
    for name, entry in zip(names, entries):
        if entry.is_remote:
            skipped_remote += 1
        elif not entry.path.is_dir():
            unavailable.append(name)
        else:
            vaults[name] = entry.path
    return VaultCatalog(vaults=vaults, skipped_remote=skipped_remote,
                        unavailable=tuple(unavailable))


def load_vault_catalog() -> VaultCatalog:
    """Build the catalog from the effective registry and the active override
    (--vault or AWIKI_VAULT), read once at startup."""
    from agent_wiki.config import load_registry, resolve_vault_override
    return build_vault_catalog(load_registry(), resolve_vault_override())
