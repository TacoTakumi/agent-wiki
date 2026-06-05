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


def test_remote_ingest_collision_raises_file_exists(remote_service, tmp_path):
    src = tmp_path / "r.md"
    src.write_text("# R\n\nv1\n")
    remote_service.ingest(src)
    clash = tmp_path / "sub" / "r.md"
    clash.parent.mkdir()
    clash.write_text("# R2\n\nx\n")
    with pytest.raises(FileExistsError):
        remote_service.ingest(clash)


def test_remote_ingest_update_rewrites(remote_service, tmp_vault, tmp_path):
    src = tmp_path / "r.md"
    src.write_text("# R\n\nv1\n")
    remote_service.ingest(src)
    src.write_text("# R\n\nv2\n")
    remote_service.ingest(src, update=True)
    assert (tmp_vault / "raw" / "r.md").read_text() == "# R\n\nv2\n"


def test_http_doctor_fix_does_not_rewrite_raw(client, tmp_vault, tmp_path, admin_h):
    # Drift a page on the SERVER vault: ingest, then hand-edit the page body.
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "d.md"
    src.write_text("# D\n\noriginal\n")
    page = ingest_file(src, tmp_vault, topic="research")
    page.write_text(page.read_text().replace("original", "edited by hand"))

    # A remote admin 'doctor --fix' must NOT rewrite raw (server-local only).
    resp = client.post("/v1/doctor", json={"fix": True}, headers=admin_h)
    assert resp.status_code == 200
    assert (tmp_vault / "raw" / "d.md").read_text() == "# D\n\noriginal\n"
