import pytest


def test_remote_search(remote_service, tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nfoo bar\n")
    out = remote_service.search("foo bar")
    assert {"all", "partial", "total"} <= set(out)


def test_remote_show_then_missing(remote_service, tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nhello\n")
    assert "hello" in remote_service.show("research/a.md")
    with pytest.raises(FileNotFoundError):
        remote_service.show("research/nope.md")


def test_remote_show_binary_raises_valueerror(remote_service, tmp_vault):
    (tmp_vault / "raw" / "x.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="cannot display binary file"):
        remote_service.show("raw/x.bin")
    assert remote_service.read_bytes("raw/x.bin") == b"\xff\xfe\x00"


def test_remote_ingest_roundtrip(remote_service, tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n\nbody\n")
    out = remote_service.ingest(src, topic="research")
    assert out["page"] == "research/doc.md"


def test_remote_permission_error_is_clickexception(server_app):
    import click
    from fastapi.testclient import TestClient
    from agent_wiki.remote import RemoteVaultService
    c = TestClient(server_app, base_url="http://test", raise_server_exceptions=False)
    reader = RemoteVaultService("http://test", "reader-tok", client=c)
    with pytest.raises(click.ClickException):
        reader.ingest_path_bytes("n.md", b"# N\n\nx\n", topic="research")
