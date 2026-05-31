import pytest

from agent_wiki.show import read_vault_file, resolve_in_vault


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


def test_read_vault_file_returns_verbatim_text(tmp_vault):
    content = "---\ntitle: Foo\n---\n\n# Foo\n\nBody.\n"
    (tmp_vault / "research" / "foo.md").write_text(content)
    assert read_vault_file(tmp_vault, "research/foo.md") == content


def test_read_vault_file_allows_any_vault_file(tmp_vault):
    # index.md is created by the tmp_vault fixture; add a raw/ file too.
    (tmp_vault / "raw" / "note.txt").write_text("raw note\n")
    assert read_vault_file(tmp_vault, "index.md") == "# Index\n"
    assert read_vault_file(tmp_vault, "raw/note.txt") == "raw note\n"


def test_read_vault_file_missing_raises(tmp_vault):
    with pytest.raises(FileNotFoundError, match="no such page"):
        read_vault_file(tmp_vault, "research/missing.md")


def test_read_vault_file_directory_raises(tmp_vault):
    with pytest.raises(FileNotFoundError, match="no such page"):
        read_vault_file(tmp_vault, "research")


def test_read_vault_file_binary_raises(tmp_vault):
    (tmp_vault / "raw" / "blob.bin").write_bytes(b"\xff\xfe\x00\x01\x80")
    with pytest.raises(ValueError, match="cannot display binary file"):
        read_vault_file(tmp_vault, "raw/blob.bin")
