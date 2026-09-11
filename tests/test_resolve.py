"""Cross-vault reference resolver: the vault: prefix parse rule plus
unique-wins / loud-ambiguity resolution across configured vaults."""

from pathlib import Path

import click
import pytest

from agent_wiki.registry import VaultEntry
from agent_wiki.resolve import resolve_ref, resolve_across_vaults, split_vault_ref


def _registry(*names):
    return {n: VaultEntry(name=n, path=Path(f"/vaults/{n}")) for n in names}


# --- split_vault_ref: the prefix parse rule -----------------------------------

def test_prefix_matching_vault_name_scopes_the_ref():
    registry = _registry("work", "personal")
    entry, ref = split_vault_ref("work:research/foo.md", registry)
    assert entry is registry["work"]
    assert ref == "research/foo.md"


def test_non_matching_prefix_leaves_whole_string_plain():
    registry = _registry("work")
    entry, ref = split_vault_ref("weird:research/foo.md", registry)
    assert entry is None
    assert ref == "weird:research/foo.md"


def test_no_colon_is_unqualified():
    registry = _registry("work")
    entry, ref = split_vault_ref("research/foo.md", registry)
    assert entry is None
    assert ref == "research/foo.md"


def test_only_first_colon_splits():
    registry = _registry("work")
    entry, ref = split_vault_ref("work:odd:name.md", registry)
    assert entry is registry["work"]
    assert ref == "odd:name.md"


# --- resolve_across_vaults: unique wins, ambiguity is loud --------------------

def _probe_in(*hits):
    """A probe that 'finds' the ref only in the named vaults."""
    def probe(entry, ref):
        return f"{entry.name}/{ref}" if entry.name in hits else None
    return probe


def test_unique_match_resolves_silently():
    registry = _registry("work", "personal")
    entry, resolved = resolve_across_vaults(
        "research/foo.md", registry, _probe_in("personal"))
    assert entry is registry["personal"]
    assert resolved == "personal/research/foo.md"


def test_ambiguous_match_errors_listing_qualified_candidates():
    registry = _registry("work", "personal")
    with pytest.raises(click.UsageError) as exc:
        resolve_across_vaults(
            "research/foo.md", registry, _probe_in("work", "personal"))
    msg = str(exc.value)
    assert "work:research/foo.md" in msg
    assert "personal:research/foo.md" in msg


def test_zero_matches_resolve_to_none():
    registry = _registry("work", "personal")
    assert resolve_across_vaults("nope.md", registry, _probe_in()) is None


# --- resolve_ref: the full rule ------------------------------------------------

def test_qualified_ref_scopes_to_the_named_vault_without_probing():
    registry = _registry("work", "personal")
    # The qualifier scopes to one vault and skips the probe entirely — the
    # caller's backend surfaces a miss itself (a remote vault's raws, for
    # instance, cannot be probed locally). A probe that finds nothing
    # therefore does not block a qualified reference.
    entry, resolved = resolve_ref(
        "personal:research/foo.md", registry, _probe_in())
    assert entry is registry["personal"]
    assert resolved == "research/foo.md"


def test_unqualified_ref_falls_through_to_cross_vault_resolution():
    registry = _registry("work", "personal")
    entry, resolved = resolve_ref(
        "research/foo.md", registry, _probe_in("work"))
    assert entry is registry["work"]
    assert resolved == "work/research/foo.md"
