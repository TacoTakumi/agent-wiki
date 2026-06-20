def test_no_token_401(client):
    assert client.get("/v1/status").status_code == 401


def test_bad_token_401(client):
    r = client.get("/v1/status", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_reader_can_read_status(client, reader_h):
    r = client.get("/v1/status", headers=reader_h)
    assert r.status_code == 200
    assert "topics" in r.json()


def test_search_endpoint(client, reader_h, tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nfoo bar\n")
    r = client.get("/v1/search", params={"q": "foo bar"}, headers=reader_h)
    assert r.status_code == 200
    body = r.json()
    assert {"all", "partial", "total", "shown", "truncated"} <= set(body)


def test_show_inline_markdown(client, reader_h, tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nhello\n")
    r = client.get("/v1/pages/research/a.md", headers=reader_h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "Content-Disposition" not in r.headers
    assert "hello" in r.text


def test_show_download_attachment(client, reader_h, tmp_vault):
    (tmp_vault / "research" / "a.md").write_text("# A\n\nhello\n")
    r = client.get("/v1/pages/research/a.md", params={"download": 1}, headers=reader_h)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]


def test_show_binary_served_as_attachment(client, reader_h, tmp_vault):
    (tmp_vault / "raw" / "x.bin").write_bytes(b"\xff\xfe\x00")
    r = client.get("/v1/pages/raw/x.bin", headers=reader_h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content == b"\xff\xfe\x00"


def test_show_missing_404(client, reader_h):
    assert client.get("/v1/pages/research/nope.md", headers=reader_h).status_code == 404


def test_show_escape_400(client, reader_h):
    r = client.get("/v1/pages/../../etc/passwd", headers=reader_h)
    assert r.status_code in (400, 404)  # path may be normalized by client/router


def test_log_lint_context(client, reader_h):
    assert client.get("/v1/log", headers=reader_h).status_code == 200
    assert client.get("/v1/lint", headers=reader_h).status_code == 200
    r = client.post("/v1/context", json={"prompt": "hi"}, headers=reader_h)
    assert r.status_code == 200
    assert "block" in r.json()


def test_ingest_json_writer(client, writer_h):
    r = client.post("/v1/ingest", json={
        "filename": "note.md", "content": "# Title\n\nbody\n",
        "topic": "research", "tags": "x,y",
    }, headers=writer_h)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Title" and body["page"] == "research/title.md"


def test_ingest_multipart_writer(client, writer_h):
    r = client.post(
        "/v1/ingest",
        files={"file": ("m.md", b"# M\n\nhi\n", "text/markdown")},
        data={"topic": "research"},
        headers=writer_h,
    )
    assert r.status_code == 201
    assert r.json()["page"] == "research/m.md"


def test_ingest_forbidden_for_reader(client, reader_h):
    r = client.post("/v1/ingest", json={"filename": "n.md", "content": "# N\n\nx\n"},
                    headers=reader_h)
    assert r.status_code == 403


def test_index_writer(client, writer_h):
    assert client.post("/v1/index", headers=writer_h).json() == {"ok": True}


def test_sync_dry_run(client, writer_h):
    r = client.post("/v1/sync", json={"dry_run": True}, headers=writer_h)
    assert r.status_code == 200
    assert "counts" in r.json()


def test_doctor_requires_admin(client, writer_h, admin_h):
    assert client.post("/v1/doctor", json={}, headers=writer_h).status_code == 403
    r = client.post("/v1/doctor", json={"fix": False}, headers=admin_h)
    assert r.status_code == 200 and "findings" in r.json()


def test_ingest_force_json(client, writer_h, tmp_vault):
    client.post("/v1/ingest", json={"filename": "f.md", "content": "# F\n\nv1\n"},
                headers=writer_h)
    page = tmp_vault / "research" / "f.md"        # default_topic is research
    page.write_text(page.read_text().replace("v1", "v1\n\nhand edit"))
    refused = client.post("/v1/ingest",
                          json={"filename": "f.md", "content": "# F\n\nv2\n",
                                "update": True}, headers=writer_h)
    assert refused.status_code == 400             # PageDriftError -> ValueError -> 400
    forced = client.post("/v1/ingest",
                         json={"filename": "f.md", "content": "# F\n\nv2\n",
                               "update": True, "force": True}, headers=writer_h)
    assert forced.status_code == 201


def test_reingest_writer_only(client, reader_h, writer_h, tmp_vault):
    client.post("/v1/ingest", json={"filename": "r.md", "content": "# R\n\nv1\n"},
                headers=writer_h)
    (tmp_vault / "raw" / "r.md").write_text("# R\n\nv2\n")
    assert client.post("/v1/reingest", json={"name": "r"},
                       headers=reader_h).status_code == 403
    refused = client.post("/v1/reingest", json={"name": "r"}, headers=writer_h)
    assert refused.status_code == 400                          # drift -> 400
    forced = client.post("/v1/reingest", json={"name": "r", "force": True},
                         headers=writer_h)
    assert forced.status_code == 201


def test_reingest_missing_404(client, writer_h):
    assert client.post("/v1/reingest", json={"name": "nope"},
                       headers=writer_h).status_code == 404
