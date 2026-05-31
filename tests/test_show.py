import pytest

from agent_wiki.show import resolve_in_vault


def test_resolve_in_vault_allows_normal_relative_path(tmp_vault):
    target = resolve_in_vault(tmp_vault, "research/foo.md")
    assert target == (tmp_vault.resolve() / "research" / "foo.md")


def test_resolve_in_vault_rejects_parent_traversal(tmp_vault):
    with pytest.raises(ValueError, match="outside the vault"):
        resolve_in_vault(tmp_vault, "../../etc/passwd")


def test_resolve_in_vault_rejects_absolute_outside(tmp_vault):
    with pytest.raises(ValueError, match="outside the vault"):
        resolve_in_vault(tmp_vault, "/etc/passwd")


def test_resolve_in_vault_allows_absolute_inside(tmp_vault):
    inside = str(tmp_vault.resolve() / "research" / "foo.md")
    target = resolve_in_vault(tmp_vault, inside)
    assert target == (tmp_vault.resolve() / "research" / "foo.md")
