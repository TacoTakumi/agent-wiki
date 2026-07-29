"""Cross-vault reference resolution.

Wherever a vault-scoped path, name, or topic is taken, a `vault:` prefix
narrows it: text before the first colon is a qualifier only when it matches a
configured vault name, otherwise the whole string is a plain reference. An
unqualified reference resolves across all configured vaults through an
injected probe — a unique match wins silently, an ambiguous one is a hard
error listing the qualified candidates.

The probe is a callable `probe(entry, ref) -> resolved | None`; resolution
semantics (file lookup, page lookup, topic lookup, remote calls) live with the
caller, not here.
"""

import click


def split_vault_ref(value: str, registry: dict) -> tuple:
    """Split `value` into (VaultEntry | None, ref) by the prefix parse rule.

    The text before the first colon is a vault qualifier only when it matches
    a configured vault name; otherwise the whole string is the reference."""
    prefix, sep, rest = value.partition(":")
    if sep and prefix in registry:
        return registry[prefix], rest
    return None, value


def resolve_across_vaults(ref: str, registry: dict, probe):
    """Resolve an unqualified reference across every configured vault.

    Returns (entry, resolved) on a unique match, None when no vault matches,
    and raises a UsageError listing the qualified candidates when more than
    one matches."""
    matches = []
    for name in sorted(registry):
        resolved = probe(registry[name], ref)
        if resolved is not None:
            matches.append((registry[name], resolved))
    if not matches:
        return None
    if len(matches) > 1:
        candidates = ", ".join(f"{e.name}:{ref}" for e, _ in matches)
        raise click.UsageError(
            f"'{ref}' is ambiguous across vaults; qualify it: {candidates}"
        )
    return matches[0]


def resolve_ref(value: str, registry: dict, probe):
    """Resolve a possibly vault-qualified reference.

    A qualified reference scopes to the named vault and is returned unprobed
    — the caller's backend surfaces a miss itself (a remote vault's raws,
    for instance, cannot be probed locally). An unqualified one resolves
    across all vaults via resolve_across_vaults."""
    entry, ref = split_vault_ref(value, registry)
    if entry is not None:
        return entry, ref
    return resolve_across_vaults(ref, registry, probe)
