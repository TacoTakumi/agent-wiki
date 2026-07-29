"""The vault registry read layer.

Parses the user config into a named map of vault entries. Two config shapes are
accepted: a `vaults:` map (each entry declares `path:` for a local vault or
`url:` plus optional `token:` for a remote one), or the legacy flat keys
(`vault_path` / `server`), which synthesize a single entry named 'main'.
Parsing is pure — it never writes the config file.
"""

from dataclasses import dataclass
from pathlib import Path

import click


LEGACY_VAULT_NAME = "main"


@dataclass(frozen=True)
class VaultEntry:
    """One named vault: a local path, a remote url+token, or (legacy-synthesized
    only) both — a legacy config may hold vault_path and server together, and
    the single 'main' entry carries both so existing precedence survives."""

    name: str
    path: "Path | None" = None
    url: "str | None" = None
    token: "str | None" = None
    # The config file that declared this entry (stamped by the loader;
    # parse_registry itself does not know the file it is parsing).
    origin: "str | None" = None

    @property
    def is_remote(self) -> bool:
        return self.url is not None


def parse_registry(config) -> dict:
    """Parse a loaded user-config dict into {name: VaultEntry}.

    A `vaults:` map wins over the legacy keys. Each explicit entry must declare
    exactly one of `path` or `url`; violations raise a UsageError naming the
    entry. With no `vaults:` map, `vault_path`/`server` synthesize a single
    entry named 'main'; an empty config yields an empty registry."""
    config = config or {}
    vaults = config.get("vaults")
    if vaults:
        return {
            str(name): _parse_entry(str(name), spec)
            for name, spec in vaults.items()
        }
    return _synthesize_legacy(config)


def resolve_default_vault(registry: dict, default_vault=None) -> VaultEntry:
    """Resolve the default vault from a parsed registry.

    Precedence: an explicit default_vault name, else the vault named 'main',
    else the sole configured vault. Multiple vaults with neither 'main' nor a
    default_vault key is a hard error, as is a default_vault naming an
    unconfigured vault. The default_vault key is read-only to the CLI: it is
    repointed by hand-editing the config only (REQ-04)."""
    if default_vault is not None:
        entry = registry.get(str(default_vault))
        if entry is None:
            raise click.UsageError(
                f"default_vault names '{default_vault}', which is not a "
                f"configured vault. Configured: "
                f"{', '.join(sorted(registry)) or 'none'}."
            )
        return entry
    if LEGACY_VAULT_NAME in registry:
        return registry[LEGACY_VAULT_NAME]
    if len(registry) == 1:
        return next(iter(registry.values()))
    if not registry:
        raise click.UsageError(
            "No vault configured. Run 'awiki init <path>' first."
        )
    raise click.UsageError(
        f"Multiple vaults configured ({', '.join(sorted(registry))}) with no "
        f"'main' entry; set default_vault in config.yaml to pick one."
    )


def _parse_entry(name: str, spec) -> VaultEntry:
    if not isinstance(spec, dict):
        raise click.UsageError(
            f"vault '{name}' in config.yaml must be a mapping with a "
            f"'path' or 'url' key."
        )
    path = spec.get("path")
    url = spec.get("url")
    if path and url:
        raise click.UsageError(
            f"vault '{name}' in config.yaml declares both 'path' and 'url'; "
            f"a vault is local (path) or remote (url), not both."
        )
    if not path and not url:
        raise click.UsageError(
            f"vault '{name}' in config.yaml declares neither 'path' nor "
            f"'url'; add one."
        )
    return VaultEntry(
        name=name,
        path=Path(str(path)).expanduser() if path else None,
        url=str(url) if url else None,
        token=str(spec["token"]) if spec.get("token") is not None else None,
    )


def _synthesize_legacy(config: dict) -> dict:
    vault_path = config.get("vault_path")
    server = config.get("server") or {}
    url = server.get("url")
    if not vault_path and not url:
        return {}
    return {
        LEGACY_VAULT_NAME: VaultEntry(
            name=LEGACY_VAULT_NAME,
            path=Path(str(vault_path)).expanduser() if vault_path else None,
            url=str(url) if url else None,
            token=(
                str(server["token"]) if server.get("token") is not None
                else None
            ),
        )
    }
