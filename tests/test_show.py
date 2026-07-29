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


# --- show surfaces the read location on stderr (T-10 / REQ-13) ---------------


def test_show_prints_read_location_to_stderr(tmp_config, tmp_vault):
    # REQ-13: show surfaces where it read from on stderr (local absolute path)
    # while stdout stays byte-identical to the underlying file — skills parse it.
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    content = "---\ntitle: Loc\ntopic: research\n---\n\n# Loc\n\nbody here\n"
    (tmp_vault / "research" / "loc.md").write_text(content)

    result = CliRunner().invoke(cli, ["show", "research/loc.md"])
    assert result.exit_code == 0, result.output
    assert result.stdout == content                                   # byte-identical
    assert str(tmp_vault / "research" / "loc.md") in result.stderr    # location -> stderr


def test_show_remote_read_location_is_server_ref(remote_service, tmp_vault, monkeypatch):
    # For a remote vault the read location is the server URL + vault-relative path,
    # never a local absolute path; stdout still equals the served bytes.
    from click.testing import CliRunner
    from agent_wiki import cli as cli_mod

    content = "# Served\n\nremote body\n"
    (tmp_vault / "research" / "srv.md").write_text(content)
    monkeypatch.setattr(cli_mod, "_service", lambda: remote_service)

    result = CliRunner().invoke(cli_mod.cli, ["show", "research/srv.md"])
    assert result.exit_code == 0, result.output
    assert result.stdout == content                     # byte-identical to served bytes
    assert remote_service.base in result.stderr         # server URL
    assert "research/srv.md" in result.stderr           # vault-relative path
    assert str(tmp_vault) not in result.stderr          # no local absolute path


# --- multi-vault dispatch (vault: prefix + cross-vault resolution) ------------

def _seed_page(vault, rel, content):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_show_qualified_ref_opens_page_in_named_vault(two_vault_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    work = tmp_path / "work-vault"
    personal = tmp_path / "personal-vault"
    _seed_page(work, "research/foo.md", "# Foo\n\nwork copy\n")
    _seed_page(personal, "research/foo.md", "# Foo\n\npersonal copy\n")

    result = CliRunner().invoke(cli, ["show", "personal:research/foo.md"])
    assert result.exit_code == 0, result.output
    assert "personal copy" in result.stdout


def test_show_unqualified_unique_match_resolves_silently(two_vault_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    personal = tmp_path / "personal-vault"
    _seed_page(personal, "research/only-here.md", "# Only\n\nfound it\n")

    result = CliRunner().invoke(cli, ["show", "research/only-here.md"])
    assert result.exit_code == 0, result.output
    assert "found it" in result.stdout


def test_show_unqualified_ambiguous_lists_qualified_candidates(two_vault_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    _seed_page(tmp_path / "work-vault", "research/dup.md", "# Dup\n\nwork\n")
    _seed_page(tmp_path / "personal-vault", "research/dup.md", "# Dup\n\npersonal\n")

    result = CliRunner().invoke(cli, ["show", "research/dup.md"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "work:research/dup.md" in combined
    assert "personal:research/dup.md" in combined


def test_show_non_matching_prefix_is_a_literal_path(two_vault_config, tmp_path):
    from click.testing import CliRunner
    from agent_wiki.cli import cli

    # A colon in the filename with no vault named 'weird': the whole string is
    # a plain path, resolved cross-vault as-is.
    _seed_page(tmp_path / "work-vault", "research/weird:name.md", "# W\n\nliteral\n")

    result = CliRunner().invoke(cli, ["show", "research/weird:name.md"])
    assert result.exit_code == 0, result.output
    assert "literal" in result.stdout
