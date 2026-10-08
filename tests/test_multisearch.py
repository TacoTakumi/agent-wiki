"""The shared cross-vault search merge, called without any CLI context."""

import os
import tempfile
from pathlib import Path

from agent_wiki.ingest import ingest_file
from agent_wiki.registry import VaultEntry

from conftest import make_vault


def _seed(vault, name, body):
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, name)
        with open(src, "w") as f:
            f.write(body)
        ingest_file(Path(src), vault)


def _two_vaults(tmp_path):
    work = make_vault(tmp_path / "work-vault")
    personal = make_vault(tmp_path / "personal-vault")
    return {
        "work": VaultEntry(name="work", path=work),
        "personal": VaultEntry(name="personal", path=personal),
    }


def test_merges_vaults_into_one_ranked_list_with_qualified_paths(tmp_path):
    from agent_wiki.multisearch import merged_search

    vaults = _two_vaults(tmp_path)
    _seed(vaults["work"].path, "one.md", "# One\n\nzebras only\n")
    _seed(vaults["personal"].path, "two.md", "# Two\n\nzebras and lions\n")

    out, skipped = merged_search(vaults, "zebras lions")
    assert skipped == []
    assert [r["path"] for r in out["all"]] == ["personal:research/two.md"]
    assert [r["path"] for r in out["partial"]] == ["work:research/one.md"]
    assert out["all"][0]["vault"] == "personal"
    assert out["partial"][0]["vault"] == "work"
    assert out["total"] == 2 and out["shown"] == 2
    assert out["truncated"] is False


def test_caps_tiers_across_vaults_and_reports_truncation(tmp_path):
    from agent_wiki.multisearch import merged_search

    vaults = _two_vaults(tmp_path)
    for i in range(3):
        _seed(vaults["work"].path, f"w{i}.md", f"# W{i}\n\nzebras\n")
        _seed(vaults["personal"].path, f"p{i}.md", f"# P{i}\n\nzebras\n")

    out, skipped = merged_search(vaults, "zebras", limit=4)
    assert len(out["all"]) == 4
    assert out["total"] == 6 and out["shown"] == 4
    assert out["truncated"] is True


def test_unreachable_vault_is_returned_as_skipped_not_printed(tmp_path, capsys):
    from agent_wiki.multisearch import merged_search

    vaults = _two_vaults(tmp_path)
    vaults["gone"] = VaultEntry(name="gone", path=tmp_path / "missing")
    _seed(vaults["work"].path, "one.md", "# One\n\nzebras\n")

    out, skipped = merged_search(vaults, "zebras")
    assert [name for name, _ in skipped] == ["gone"]
    assert skipped[0][1]
    assert [r["path"] for r in out["all"]] == ["work:research/one.md"]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_vault_qualified_topic_narrows_to_that_vault(tmp_path):
    from agent_wiki.multisearch import merged_search

    vaults = _two_vaults(tmp_path)
    _seed(vaults["work"].path, "one.md", "# One\n\nzebras\n")
    _seed(vaults["personal"].path, "two.md", "# Two\n\nzebras\n")

    out, _ = merged_search(vaults, "zebras", topic="personal:research")
    assert [r["path"] for r in out["all"]] == ["personal:research/two.md"]
