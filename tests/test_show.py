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


def test_resolve_in_vault_rejects_escaping_symlink(tmp_path, tmp_vault):
    # A symlink that lives inside the vault but points outside it must be rejected,
    # because resolve() follows the link before the confinement check.
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret\n")
    link = tmp_vault / "research" / "link.md"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside the vault"):
        resolve_in_vault(tmp_vault, "research/link.md")
