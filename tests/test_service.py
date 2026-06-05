import pytest

from agent_wiki.service import LocalVaultService
from agent_wiki.ingest import ingest_file


def _svc(vault):
    return LocalVaultService(vault)


def test_vault_path_attribute(tmp_vault):
    assert _svc(tmp_vault).vault_path == tmp_vault


def test_show_reads_text(tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nhello\n")
    assert "hello" in _svc(tmp_vault).show("research/a.md")


def test_show_missing_raises(tmp_vault):
    with pytest.raises(FileNotFoundError):
        _svc(tmp_vault).show("research/nope.md")


def test_show_binary_raises_valueerror(tmp_vault):
    (tmp_vault / "raw" / "x.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="cannot display binary file"):
        _svc(tmp_vault).show("raw/x.bin")


def test_read_bytes_returns_raw_bytes(tmp_vault):
    (tmp_vault / "raw" / "x.bin").write_bytes(b"\xff\xfe\x00")
    assert _svc(tmp_vault).read_bytes("raw/x.bin") == b"\xff\xfe\x00"


def test_log_passthrough(tmp_vault):
    src = tmp_vault / "note.md"
    src.write_text("# Note\n\nbody\n")
    ingest_file(src, tmp_vault, topic="research")
    assert _svc(tmp_vault).log(last=1)  # one entry exists


def test_lint_passthrough_returns_list(tmp_vault):
    assert isinstance(_svc(tmp_vault).lint(), list)


def test_context_returns_str(tmp_vault):
    assert isinstance(_svc(tmp_vault).context("anything"), str)
