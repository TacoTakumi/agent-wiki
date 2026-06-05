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


def _seed_pages(vault):
    (vault / "research" / "alpha.md").write_text("# Alpha\n\nfoo bar baz\n")
    (vault / "research" / "beta.md").write_text("# Beta\n\nfoo only\n")


def test_search_splits_all_and_partial(tmp_vault):
    _seed_pages(tmp_vault)
    out = _svc(tmp_vault).search("foo bar")
    titles_all = {r["title"] for r in out["all"]}
    titles_partial = {r["title"] for r in out["partial"]}
    # Hand-written files lack frontmatter, so the search title falls back to the
    # filename stem (existing search behavior — title casing is incidental here).
    assert "alpha" in titles_all          # has both terms
    assert "beta" in titles_partial       # has only "foo"
    assert out["total"] == 2


def test_search_caps_and_truncation(tmp_vault):
    for i in range(5):
        (tmp_vault / "research" / f"p{i}.md").write_text(f"# P{i}\n\nfoo\n")
    out = _svc(tmp_vault).search("foo", limit=2)
    assert len(out["all"]) == 2
    assert out["shown"] == 2
    assert out["total"] == 5
    assert out["truncated"] is True


def test_search_empty(tmp_vault):
    out = _svc(tmp_vault).search("zzzznomatch")
    assert out == {"all": [], "partial": [], "total": 0, "shown": 0, "truncated": False}


def test_status_counts(tmp_vault):
    src = tmp_vault / "note.md"
    src.write_text("# Note\n\nbody\n")
    ingest_file(src, tmp_vault, topic="research")
    st = _svc(tmp_vault).status()
    assert st["vault"] == str(tmp_vault)
    assert st["total"] == 1
    assert {"topic": "research", "count": 1} in st["topics"]
    assert st["raw"] == 1
    assert st["sessions_synced"] == 0
    assert st["last_activity"]  # most recent log entry string
