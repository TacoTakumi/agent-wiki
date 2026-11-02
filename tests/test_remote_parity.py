import inspect

import pytest
import yaml

from agent_wiki.page import parse_page, sidecar_path
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


def test_reingest_parity(remote_service, tmp_vault):
    from agent_wiki.service import LocalVaultService
    src = tmp_vault / "p.md"
    src.write_text("# P\n\nv1\n")
    LocalVaultService(tmp_vault).ingest(src, topic="research")
    (tmp_vault / "raw" / "p.md").write_text("# P\n\nv2\n")
    out = remote_service.reingest("p", force=True)
    assert out["page"] == "research/p.md"
    assert "v2" in (tmp_vault / "research" / "p.md").read_text()


def test_url_ingest_parity(remote_service, tmp_vault, monkeypatch, url_fetcher_cls):
    # Remote URL ingest: the client fetches+extracts and ships extracted markdown +
    # the original asset to the server, which produces the SAME result a local URL
    # ingest would — page body, inline source_url, http sidecar, archived asset.
    import agent_wiki.fetch as fetchmod
    monkeypatch.setattr(fetchmod, "HttpFetcher", url_fetcher_cls)
    url = "https://example.com/sample"

    out = remote_service.ingest_url(url, topic="research")
    assert out["page"] == "research/sample.md"
    assert out["sources"] == ["raw/sample.md"]

    parsed = parse_page(tmp_vault / "research" / "sample.md")
    assert "parity widgets" in parsed["body"]      # main content extracted
    assert "NAVZZZ" not in parsed["body"]           # boilerplate stripped
    assert parsed["meta"]["source_url"] == url

    raw = tmp_vault / "raw" / "sample.md"
    sidecar = yaml.safe_load(sidecar_path(raw).read_text())
    assert sidecar["source"] == url
    assert sidecar["fetcher"] == "http"

    # The original HTML asset was archived byte-identically — by the server, from
    # the bytes the client delivered.
    asset = tmp_vault / "raw" / "assets" / "sample.html"
    assert asset.read_bytes() == url_fetcher_cls.HTML.encode()


def test_url_ingest_server_makes_no_outbound_fetch(remote_service, tmp_vault,
                                                   monkeypatch, url_fetcher_cls):
    # Behavioral proof: the client uses the canned fetcher; a real httpx
    # GET anywhere (client OR server) trips the guard. A clean ingest means the
    # server reached the network not at all.
    import httpx
    import agent_wiki.fetch as fetchmod
    monkeypatch.setattr(fetchmod, "HttpFetcher", url_fetcher_cls)

    def _boom(*args, **kwargs):
        raise AssertionError("no outbound HTTP fetch expected for URL ingest")
    monkeypatch.setattr(httpx, "get", _boom)

    out = remote_service.ingest_url("https://example.com/sample", topic="research")
    assert out["page"] == "research/sample.md"


def test_url_ingest_handlers_invoke_no_fetcher():
    # Structural proof: neither the server route nor the server-side
    # ingest seam constructs a Fetcher; the remote client delegates fetching to the
    # client-side fetch_and_extract seam rather than touching a Fetcher inline.
    from agent_wiki import remote
    from agent_wiki.ingest import ingest_extracted
    from agent_wiki.server import routes

    src = (
        inspect.getsource(remote.RemoteVaultService.ingest_url)
        + inspect.getsource(routes.build_router)
        + inspect.getsource(ingest_extracted)
    )
    # No Fetcher construction (`SomethingFetcher(...)`) and no `.fetch(...)` call —
    # fetching is delegated to the client-side fetch_and_extract seam. (Prose that
    # merely names "Fetcher" is fine; we forbid the call, not the word.)
    assert "Fetcher(" not in src, "URL-ingest handlers must not construct a Fetcher"
    assert ".fetch(" not in src, "URL-ingest handlers must not invoke a Fetcher"


def test_url_ingest_unchanged_skip_round_trips(remote_service, tmp_vault,
                                               monkeypatch, url_fetcher_cls):
    # An unchanged re-ingest raises UnchangedURLSkip on BOTH local and remote, so
    # the CLI's existing skip handling works identically against a remote vault.
    import agent_wiki.fetch as fetchmod
    from agent_wiki.ingest import UnchangedURLSkip
    monkeypatch.setattr(fetchmod, "HttpFetcher", url_fetcher_cls)
    url = "https://example.com/sample"

    remote_service.ingest_url(url, topic="research")
    with pytest.raises(UnchangedURLSkip):
        remote_service.ingest_url(url, topic="research")


SLICING_PAGE = """---
title: Ops
topic: research
tags: []
---

# Ops

Page preamble.

## Check log

Log preamble.

### Entry 1

Body 1.

### Entry 2

Body 2.

## Notes

Notes body.
"""

SLICING_FLAGS = [
    ["--outline"],
    ["--section", "check log"],
    ["--section", "check log", "--outline"],
    ["--head", "1"],
    ["--tail", "1"],
    ["--section", "check log", "--head", "1"],
    ["--section", "check log", "--tail", "1"],
]


def _show_stdout(service, monkeypatch, args):
    from click.testing import CliRunner
    from agent_wiki import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_service", lambda: service)
    result = CliRunner().invoke(cli_mod.cli, ["show", *args])
    assert result.exit_code == 0, result.output
    return result.stdout


@pytest.mark.parametrize("flags", SLICING_FLAGS)
def test_show_slicing_parity(remote_service, tmp_vault, monkeypatch, flags):
    # Slicing runs client-side on the page text a vault service returns, so a
    # remote vault prints byte-identically to a local one for the same flags.
    (tmp_vault / "research" / "log.md").write_text(SLICING_PAGE)
    args = ["research/log.md", *flags]
    local = _show_stdout(LocalVaultService(tmp_vault), monkeypatch, args)
    remote = _show_stdout(remote_service, monkeypatch, args)
    assert remote == local
    assert local != ""


def test_show_slicing_issues_one_page_get_only(remote_service, tmp_vault,
                                               monkeypatch):
    # The slicing flags add no HTTP surface: the remote show path still makes
    # exactly the one page request it always made.
    (tmp_vault / "research" / "log.md").write_text(SLICING_PAGE)
    calls = []
    original = remote_service._c.request

    def _record(method, url, *args, **kwargs):
        calls.append((method, str(url)))
        return original(method, url, *args, **kwargs)

    monkeypatch.setattr(remote_service._c, "request", _record)
    _show_stdout(remote_service, monkeypatch,
                 ["research/log.md", "--section", "check log", "--head", "1"])
    assert calls == [("GET", "/v1/pages/research/log.md")]


# --- converted documents (EPUB, HTML, PDF) through the server -----------------

def _ingest_cli(service, monkeypatch, path, topic="research"):
    """Run `awiki ingest <path> --topic <topic>` against ``service``."""
    from click.testing import CliRunner
    from agent_wiki import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ingest_service", lambda t: (service, t))
    return CliRunner().invoke(cli_mod.cli, ["ingest", str(path), "--topic", topic])


def _vault_tree(vault):
    """Every file under ``vault`` with its content, minus what depends on the
    clock: a sidecar's ``ingested`` time and the log line's timestamp."""
    import re
    tree = {}
    for f in sorted(p for p in vault.rglob("*") if p.is_file()):
        rel = f.relative_to(vault).as_posix()
        if rel.endswith(".meta.yaml"):
            meta = yaml.safe_load(f.read_text())
            meta.pop("ingested", None)
            tree[rel] = meta
        elif rel == "log.md":
            tree[rel] = re.sub(r"\*\*[0-9: -]+\*\*", "**TIME**", f.read_text())
        else:
            tree[rel] = f.read_bytes()
    return tree


def _record_requests(monkeypatch, service):
    """Record every request the remote client sends (method, path, headers, body)."""
    sent = []
    original = service._c.send

    def _send(request, *args, **kwargs):
        sent.append(request)
        return original(request, *args, **kwargs)

    monkeypatch.setattr(service._c, "send", _send)
    return sent


def _book(tmp_path):
    from convert_fixtures import build_epub
    return build_epub(tmp_path / "books" / "book.epub")


def test_epub_ingest_parity(remote_service, tmp_vault, tmp_path, monkeypatch):
    # The same EPUB ingested into a local vault and through the server: the
    # vault trees match file for file (sidecars included, the source path being
    # the client's path in both) and the CLI prints the same line.
    from conftest import make_vault
    book = _book(tmp_path)
    local_vault = make_vault(tmp_path / "local-vault")

    local = _ingest_cli(LocalVaultService(local_vault), monkeypatch, book)
    remote = _ingest_cli(remote_service, monkeypatch, book)

    assert local.exit_code == 0, local.output
    assert remote.exit_code == 0, remote.output
    assert local.stdout == "Ingested book.epub -> research/a-book.md (3 chapters)\n"
    assert remote.stdout == local.stdout

    assert _vault_tree(tmp_vault) == _vault_tree(local_vault)
    side = yaml.safe_load(
        sidecar_path(tmp_vault / "raw" / "a-book" / "01-chapter-one.md").read_text())
    assert side["source"] == str(book)


def test_epub_ingest_request_body_is_the_raw_file(remote_service, tmp_path,
                                                  monkeypatch):
    # The client uploads the file's own bytes; the server converts. No
    # client-side markdown travels in the request.
    book = _book(tmp_path)
    sent = _record_requests(monkeypatch, remote_service)

    out = remote_service.ingest(book, topic="research")

    assert out["page"] == "research/a-book.md"
    assert out["chapters"] == 3
    [request] = sent
    assert (request.method, request.url.path) == ("POST", "/v1/ingest")
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.read()
    assert book.read_bytes() in body
    assert b'name="markdown"' not in body
    assert b"BODY1" not in body.replace(book.read_bytes(), b"")


def test_single_chapter_document_parity(remote_service, tmp_vault, tmp_path,
                                        monkeypatch):
    # A one-chapter document is one page: both vaults print the unchanged
    # single-page line, with no chapter count, and hold the same files.
    from convert_fixtures import build_html
    from conftest import make_vault
    src = build_html(tmp_path / "docs" / "one.html",
                     sections=[(1, "Only Heading", "Body.")], image=None)
    local_vault = make_vault(tmp_path / "local-vault")

    local = _ingest_cli(LocalVaultService(local_vault), monkeypatch, src)
    remote = _ingest_cli(remote_service, monkeypatch, src)

    assert local.exit_code == 0, local.output
    assert local.stdout == "Ingested one.html -> research/sample-document.md\n"
    assert remote.stdout == local.stdout
    assert _vault_tree(tmp_vault) == _vault_tree(local_vault)


def test_mismatched_document_error_parity(remote_service, tmp_vault, tmp_path,
                                          monkeypatch):
    # A file whose content does not match its extension: the server's
    # conversion error reaches the CLI as the same one-line error and exit 1,
    # and the remote vault is left untouched.
    from conftest import make_vault
    bad = tmp_path / "docs" / "bad.epub"
    bad.parent.mkdir()
    bad.write_bytes(b"this is not a zip archive")
    local_vault = make_vault(tmp_path / "local-vault")
    before = _vault_tree(tmp_vault)

    local = _ingest_cli(LocalVaultService(local_vault), monkeypatch, bad)
    remote = _ingest_cli(remote_service, monkeypatch, bad)

    assert local.exit_code == remote.exit_code == 1
    assert local.stderr.startswith("error: could not ingest bad.epub: ")
    assert remote.stderr == local.stderr
    assert remote.stdout == local.stdout == ""
    assert _vault_tree(tmp_vault) == before


def test_markdown_ingest_records_the_client_path(remote_service, tmp_vault,
                                                 tmp_path):
    # A verbatim file sent through the server records the client's path as
    # its source, as a local ingest does, not the server's upload temp file.
    note = tmp_path / "notes" / "n.md"
    note.parent.mkdir()
    note.write_text("# N\n\nx\n")

    out = remote_service.ingest(note, topic="research")

    assert out["chapters"] == 1
    side = yaml.safe_load(sidecar_path(tmp_vault / "raw" / "n.md").read_text())
    assert side["source"] == str(note)
