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
