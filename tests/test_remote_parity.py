from agent_wiki.service import LocalVaultService


def test_search_parity(remote_service, tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nfoo bar\n")
    local = LocalVaultService(tmp_vault).search("foo bar")
    remote = remote_service.search("foo bar")
    assert local["total"] == remote["total"]
    assert [r["path"] for r in local["all"]] == [r["path"] for r in remote["all"]]


def test_status_parity(remote_service, tmp_vault):
    local = LocalVaultService(tmp_vault).status()
    remote = remote_service.status()
    assert local["topics"] == remote["topics"]
    assert local["total"] == remote["total"]


def test_lint_parity(remote_service, tmp_vault):
    assert LocalVaultService(tmp_vault).lint() == remote_service.lint()
