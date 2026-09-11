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
