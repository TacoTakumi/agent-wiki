"""Per-vault access model: role ladder, entry resolution, effective role."""
import pytest

from agent_wiki.server.access import (
    ROLES, at_least, effective_role, is_role, readable_vaults, resolve,
    role_rank,
)


def test_ladder_order():
    assert ROLES == ("none", "reader", "writer", "admin")
    ranks = [role_rank(r) for r in ROLES]
    assert ranks == sorted(ranks) and len(set(ranks)) == 4


def test_role_rank_unknown_raises():
    with pytest.raises(ValueError):
        role_rank("owner")


def test_is_role():
    for r in ROLES:
        assert is_role(r)
    assert not is_role("owner")
    assert not is_role("")
    assert not is_role("Admin")


def test_at_least():
    assert at_least("admin", "writer")
    assert at_least("writer", "writer")
    assert not at_least("reader", "writer")
    assert not at_least("none", "reader")
    assert at_least("none", "none")


def test_resolve_specific_entry_beats_wildcard():
    assert resolve({"*": "admin", "main": "reader"}, "main") == "reader"
    assert resolve({"*": "reader", "main": "admin"}, "main") == "admin"


def test_resolve_falls_back_to_wildcard():
    assert resolve({"*": "writer"}, "other") == "writer"


def test_resolve_missing_entry_is_none():
    assert resolve({"main": "writer"}, "other") == "none"
    assert resolve({}, "main") == "none"
    assert resolve(None, "main") == "none"


def test_no_scope_resolves_to_admin_cap():
    # A missing scope map does not narrow: the grant passes unchanged.
    for role in ROLES:
        assert effective_role({"main": role}, None, "main") == role


def test_effective_is_min_of_grant_and_scope():
    assert effective_role({"main": "admin"}, {"main": "reader"}, "main") == "reader"
    assert effective_role({"main": "reader"}, {"main": "admin"}, "main") == "reader"
    assert effective_role({"main": "writer"}, {"main": "writer"}, "main") == "writer"


def test_example_wildcard_writer_with_personal_none():
    grants = {"*": "writer", "personal": "none"}
    assert effective_role(grants, None, "main") == "writer"
    assert effective_role(grants, None, "personal") == "none"


def test_example_admin_grant_scoped_to_main_reader():
    grants, scope = {"*": "admin"}, {"main": "reader"}
    assert effective_role(grants, scope, "main") == "reader"
    assert effective_role(grants, scope, "other") == "none"


def test_example_scope_cannot_widen_grant():
    assert effective_role({"main": "reader"}, {"*": "admin"}, "main") == "reader"
    assert effective_role({"main": "reader"}, {"*": "admin"}, "other") == "none"


def test_example_no_scope_gives_grant_unchanged():
    grants = {"*": "reader", "main": "writer"}
    assert effective_role(grants, None, "main") == "writer"
    assert effective_role(grants, None, "other") == "reader"


def test_an_unknown_stored_role_resolves_to_none():
    assert resolve({"main": "owner"}, "main") == "none"
    assert resolve({"*": "owner"}, "other") == "none"
    assert effective_role({"*": "writer"}, {"main": "owner"}, "main") == "none"
    assert readable_vaults({"main": "owner", "notes": "reader"},
                           ["main", "notes"]) == ["notes"]
