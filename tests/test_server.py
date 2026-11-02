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
    # Hand-edit the PAGE body (not the raw) so it genuinely drifts from its stamp;
    # a raw-only edit would now reingest cleanly.
    page = tmp_vault / "research" / "r.md"                     # default_topic research
    page.write_text(page.read_text().replace("v1", "v1\n\nhand edit"))
    assert client.post("/v1/reingest", json={"name": "r"},
                       headers=reader_h).status_code == 403
    refused = client.post("/v1/reingest", json={"name": "r"}, headers=writer_h)
    assert refused.status_code == 400                          # page drift -> 400
    forced = client.post("/v1/reingest", json={"name": "r", "force": True},
                         headers=writer_h)
    assert forced.status_code == 201


def test_reingest_missing_404(client, writer_h):
    assert client.post("/v1/reingest", json={"name": "nope"},
                       headers=writer_h).status_code == 404


def test_session_titles_doctor_fix_through_the_server(client, admin_h, tmp_vault):
    from agent_wiki.conversation import Conversation, ingest_conversation, write_bundle
    from agent_wiki.page import parse_page

    body = (
        "## [10:00:00] user\n\n<local-command-caveat>Caveat: generated by the user "
        "while running local commands.</local-command-caveat>\n\n"
        "## [10:00:01] user\n\nFix the login bug\n"
    )
    conv = Conversation(agent="claude-code", session_id="bad", body=body,
                        title="<local-command-caveat>Caveat: generated by the user whil")
    page = ingest_conversation(write_bundle(conv, tmp_vault), tmp_vault)

    r = client.post("/v1/doctor", json={"fix": True}, headers=admin_h)

    assert r.status_code == 200
    assert "session-titles" in [f["name"] for f in r.json()["findings"]]
    assert parse_page(page)["meta"]["title"] == "Fix the login bug"


def test_ingest_multipart_epub_returns_chapter_count(client, writer_h, tmp_path):
    from convert_fixtures import build_epub
    book = build_epub(tmp_path / "book.epub")
    r = client.post(
        "/v1/ingest",
        files={"file": ("book.epub", book.read_bytes(), "application/octet-stream")},
        data={"topic": "research"},
        headers=writer_h,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["page"] == "research/a-book.md"
    assert body["chapters"] == 3


def test_ingest_json_markdown_returns_one_chapter(client, writer_h):
    r = client.post("/v1/ingest", json={"filename": "c.md", "content": "# C\n\nx\n"},
                    headers=writer_h)
    assert r.status_code == 201
    assert r.json()["chapters"] == 1


def test_ingest_records_the_client_source_path(client, writer_h, tmp_vault, tmp_path):
    # The upload lands in a server temp file; the sidecar records the path the
    # client sent instead, for a converted document and a verbatim file alike.
    import yaml
    from convert_fixtures import build_epub
    book = build_epub(tmp_path / "book.epub")
    client.post(
        "/v1/ingest",
        files={"file": ("book.epub", book.read_bytes(), "application/octet-stream")},
        data={"topic": "research", "source_path": "/home/you/books/book.epub"},
        headers=writer_h,
    )
    client.post("/v1/ingest", json={"filename": "s.md", "content": "# S\n\nx\n",
                                    "source_path": "notes/s.md"}, headers=writer_h)
    chapter = tmp_vault / "raw" / "a-book" / "01-chapter-one.meta.yaml"
    note = tmp_vault / "raw" / "s.meta.yaml"
    assert yaml.safe_load(chapter.read_text())["source"] == "/home/you/books/book.epub"
    assert yaml.safe_load(note.read_text())["source"] == "notes/s.md"


def test_ingest_mismatched_document_is_400(client, writer_h, tmp_vault):
    r = client.post(
        "/v1/ingest",
        files={"file": ("bad.epub", b"not a zip", "application/octet-stream")},
        headers=writer_h,
    )
    assert r.status_code == 400
    assert not (tmp_vault / "raw" / "assets" / "bad.epub").exists()
