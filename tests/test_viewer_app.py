"""Viewer routes mounted into the server app: switched on by a catalog, served
without a token, and absent (404) when the viewer is off."""

import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from agent_wiki.cli import cli
from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.viewer.catalog import PageRecord, build_vault_catalog
from agent_wiki.viewer.routes import by_recency
from conftest import make_vault


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731, "tokens": []}


@pytest.fixture
def catalog(tmp_path):
    """Two local vaults that exist, one remote, one local whose path is gone."""
    registry = {
        "main": VaultEntry(name="main", path=make_vault(tmp_path / "main")),
        "work": VaultEntry(name="work", path=make_vault(tmp_path / "work")),
        "team": VaultEntry(name="team", url="https://wiki.example.com",
                           token="tok-1"),
        "gone": VaultEntry(name="gone", path=tmp_path / "gone"),
    }
    return build_vault_catalog(registry)


@pytest.fixture
def viewer(tmp_vault, catalog):
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def no_viewer(tmp_vault):
    app = create_app(tmp_vault, SERVER_CONFIG)
    return TestClient(app, raise_server_exceptions=False)


def test_vault_list_is_html_without_a_token(viewer):
    resp = viewer.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_vault_list_links_local_vaults(viewer):
    html = viewer.get("/").text
    assert 'href="/vaults/main/"' in html
    assert 'href="/vaults/work/"' in html


def test_vault_list_does_not_link_remote_or_missing_vaults(viewer):
    html = viewer.get("/").text
    assert "/vaults/team/" not in html
    assert "/vaults/gone/" not in html


def test_vault_list_counts_skipped_remote_vaults(viewer):
    assert "1 remote vault skipped" in viewer.get("/").text


def test_vault_list_marks_missing_vaults_unavailable(viewer):
    html = viewer.get("/").text
    assert "gone" in html
    assert "unavailable" in html


def test_served_vault_route_answers(viewer):
    assert viewer.get("/vaults/main/").status_code == 200


@pytest.mark.parametrize("name", ["team", "gone", "nosuch"])
def test_remote_missing_and_unknown_vaults_return_404(viewer, name):
    assert viewer.get(f"/vaults/{name}/").status_code == 404


def test_api_still_needs_a_token_with_the_viewer_on(viewer):
    assert viewer.get("/v1/status").status_code == 401


def test_viewer_off_serves_no_viewer_routes(no_viewer):
    assert no_viewer.get("/").status_code == 404
    assert no_viewer.get("/vaults/main/").status_code == 404


def test_viewer_routes_stay_out_of_the_api_schema(tmp_vault, catalog):
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    assert all(p.startswith("/v1/") for p in app.openapi()["paths"])


def test_serve_help_lists_viewer_flag():
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--viewer" in result.output


PAGE = """---
title: Foo Page
topic: research
tags: [alpha, beta]
created: 2026-01-02
updated: 2026-03-04
sources:
- raw/x.md
- https://example.com/a
---

# Foo Page

See [[Bar Page]] and [[Nowhere]].

| a | b |
|---|---|
| 1 | 2 |

```python
print("hi")
```
"""

NESTED = """---
title: Bar Page
topic: research
---

Nested body.
"""


@pytest.fixture
def pages(catalog):
    """Pages in the main vault: one with everything the view shows, one nested
    under a topic, and files the viewer must not serve."""
    main = catalog.vaults["main"]
    (main / "research" / "foo.md").write_text(PAGE)
    (main / "research" / "sub").mkdir()
    (main / "research" / "sub" / "bar.md").write_text(NESTED)
    (main / "raw" / "x.md").write_text("# Raw\n")
    (main / "notes").mkdir()
    (main / "notes" / "loose.md").write_text(NESTED)
    (main / "research" / "plain.txt").write_text("text\n")
    (main / "research" / "nofm.md").write_text("# No frontmatter\n")
    outside = main.parent / "outside.md"
    outside.write_text(NESTED)
    (main / "research" / "escape.md").symlink_to(outside)
    return main


def test_page_view_shows_metadata(viewer, pages):
    resp = viewer.get("/vaults/main/research/foo")
    assert resp.status_code == 200
    html = resp.text
    assert "<title>Foo Page - Agent Wiki</title>" in html
    assert "research" in html
    assert "2026-01-02" in html
    assert "2026-03-04" in html
    assert "alpha" in html
    assert "beta" in html


def test_page_view_renders_table_and_code(viewer, pages):
    html = viewer.get("/vaults/main/research/foo").text
    assert "<table>" in html
    assert "<pre><code" in html
    assert "print(&quot;hi&quot;)" in html or 'print("hi")' in html


def test_page_view_resolves_wikilinks_in_the_same_vault(viewer, pages):
    html = viewer.get("/vaults/main/research/foo").text
    assert '<a href="/vaults/main/research/sub/bar"' in html
    assert ">Bar Page</a>" in html
    assert '<span class="missing">Nowhere</span>' in html


def test_page_view_links_web_sources_and_not_raw_ones(viewer, pages):
    html = viewer.get("/vaults/main/research/foo").text
    assert '<a href="https://example.com/a">https://example.com/a</a>' in html
    assert "raw/x.md" in html
    assert 'href="raw/x.md"' not in html
    assert ">raw/x.md</a>" not in html


def test_nested_page_is_reachable(viewer, pages):
    resp = viewer.get("/vaults/main/research/sub/bar")
    assert resp.status_code == 200
    assert "Nested body." in resp.text


@pytest.mark.parametrize("path", [
    "raw/x",
    "raw/x.md",
    "wiki.yaml",
    "log",
    "index",
    "notes/loose",
    "research/plain.txt",
    "research/plain",
    "research/nosuch",
    "research/nofm",
    "research/escape",
    "research/..%2F..%2Fetc%2Fpasswd",
    "research/%2E%2E/%2E%2E/outside",
])
def test_unservable_paths_return_404(viewer, pages, path):
    assert viewer.get(f"/vaults/main/{path}").status_code == 404


def test_page_in_an_unserved_vault_returns_404(viewer, pages):
    assert viewer.get("/vaults/team/research/foo").status_code == 404
    assert viewer.get("/vaults/gone/research/foo").status_code == 404


def dated_page(title, topic, updated="", created=""):
    lines = ["---", f"title: {title}", f"topic: {topic}"]
    if created:
        lines.append(f"created: '{created}'")
    if updated:
        lines.append(f"updated: '{updated}'")
    return "\n".join(lines + ["---", "", f"# {title}", ""])


def page_links(html, name="main"):
    """The page links in a listing, in order: viewer hrefs that name a page,
    not a topic (topic URLs end with a slash)."""
    return re.findall(rf'href="(/vaults/{name}/[^"?]*[^/"])"', html)


@pytest.fixture
def home_pages(catalog):
    """25 dated pages across research and tools, plus a .md file without
    frontmatter that is not a page."""
    main = catalog.vaults["main"]
    for day in range(1, 26):
        topic = "research" if day % 5 else "tools"
        (main / topic / f"p{day:02d}.md").write_text(
            dated_page(f"Page {day:02d}", topic, updated=f"2026-01-{day:02d}"))
    (main / "research" / "nofm.md").write_text("# No frontmatter\n")
    return main


def test_vault_home_shows_topics_with_page_counts(viewer, home_pages):
    html = viewer.get("/vaults/main/").text
    assert '<a href="/vaults/main/research/">research</a> (20)' in html
    assert '<a href="/vaults/main/tools/">tools</a> (5)' in html
    assert '<a href="/vaults/main/projects/">projects</a> (0)' in html


def test_vault_home_lists_the_20_most_recent_pages(viewer, home_pages):
    links = page_links(viewer.get("/vaults/main/").text)
    expected = []
    for day in range(25, 5, -1):
        topic = "research" if day % 5 else "tools"
        expected.append(f"/vaults/main/{topic}/p{day:02d}")
    assert links == expected


def test_vault_home_has_a_search_form_for_this_vault(viewer, home_pages):
    html = viewer.get("/vaults/main/").text
    assert '<form method="get" action="/search">' in html
    assert 'name="q"' in html
    assert '<input type="hidden" name="vault" value="main">' in html


def record(title, updated="", created=""):
    return PageRecord(title=title, topic="research", tags=(), created=created,
                      updated=updated, url_path=f"research/{title}",
                      path=Path(f"{title}.md"))


def test_recency_sorts_updated_then_created_then_title_undated_last():
    records = [
        record("undated"),
        record("created-only", created="2026-05-01"),
        record("b-same", updated="2026-02-01", created="2026-01-01"),
        record("a-same", updated="2026-02-01", created="2026-01-01"),
        record("newer-created", updated="2026-02-01", created="2026-01-09"),
        record("newest", updated="2026-03-01"),
    ]
    assert [r.title for r in by_recency(records)] == [
        "newest", "newer-created", "a-same", "b-same", "created-only",
        "undated"]


@pytest.fixture
def many_pages(catalog):
    """120 dated pages in research."""
    main = catalog.vaults["main"]
    for n in range(120):
        (main / "research" / f"p{n:03d}.md").write_text(dated_page(
            f"Page {n:03d}", "research",
            updated=f"2026-{n // 28 + 1:02d}-{n % 28 + 1:02d}"))
    return main


def test_topic_first_screen_has_50_pages_and_a_next_link_only(viewer,
                                                              many_pages):
    resp = viewer.get("/vaults/main/research/")
    assert resp.status_code == 200
    links = page_links(resp.text)
    assert len(links) == 50
    assert links[0] == "/vaults/main/research/p119"
    assert 'rel="next" href="?page=2"' in resp.text
    assert 'rel="prev"' not in resp.text


def test_topic_middle_screen_links_both_ways(viewer, many_pages):
    html = viewer.get("/vaults/main/research/?page=2").text
    assert len(page_links(html)) == 50
    assert 'rel="prev" href="?page=1"' in html
    assert 'rel="next" href="?page=3"' in html


def test_topic_last_screen_has_the_rest_and_a_previous_link_only(viewer,
                                                                many_pages):
    resp = viewer.get("/vaults/main/research/?page=3")
    assert resp.status_code == 200
    links = page_links(resp.text)
    assert len(links) == 20
    assert links[-1] == "/vaults/main/research/p000"
    assert 'rel="prev" href="?page=2"' in resp.text
    assert 'rel="next"' not in resp.text


@pytest.mark.parametrize("query", ["?page=4", "?page=0", "?page=-1"])
def test_topic_screen_out_of_range_returns_404(viewer, many_pages, query):
    assert viewer.get(f"/vaults/main/research/{query}").status_code == 404


def test_unknown_topic_returns_404(viewer, many_pages):
    assert viewer.get("/vaults/main/nosuchtopic/").status_code == 404
    assert viewer.get("/vaults/team/research/").status_code == 404


def test_empty_topic_shows_no_pages_and_no_pager(viewer, many_pages):
    resp = viewer.get("/vaults/main/projects/")
    assert resp.status_code == 200
    assert page_links(resp.text) == []
    assert 'rel="next"' not in resp.text
    assert 'rel="prev"' not in resp.text
