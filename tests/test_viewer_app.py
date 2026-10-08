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


def tagged_page(title, tags, updated):
    lines = ["---", f"title: {title}", "topic: research",
             f"updated: '{updated}'"]
    if tags:
        lines.append("tags: [" + ", ".join(f"'{t}'" for t in tags) + "]")
    return "\n".join(lines + ["---", "", f"# {title}", ""])


@pytest.fixture
def tag_pages(catalog):
    """Two pages tagged Foo and foo, one untagged, and one in another topic
    with a tag that needs URL quoting."""
    main = catalog.vaults["main"]
    (main / "research" / "older.md").write_text(
        tagged_page("Older", ["Foo"], "2026-01-01"))
    (main / "research" / "newer.md").write_text(
        tagged_page("Newer", ["foo", "bar"], "2026-02-01"))
    (main / "research" / "untagged.md").write_text(
        tagged_page("Untagged", [], "2026-03-01"))
    (main / "tools" / "spaced.md").write_text(
        tagged_page("Spaced", ["machine learning", "c++ & co"], "2026-04-01"))
    return main


def test_tag_page_lists_pages_with_the_tag_any_case_newest_first(viewer,
                                                                 tag_pages):
    resp = viewer.get("/vaults/main/-/tags/foo")
    assert resp.status_code == 200
    assert page_links(resp.text) == ["/vaults/main/research/newer",
                                     "/vaults/main/research/older"]


def test_page_view_links_each_tag_to_its_tag_page(viewer, tag_pages):
    html = viewer.get("/vaults/main/research/newer").text
    assert '<a href="/vaults/main/-/tags/foo">foo</a>' in html
    assert '<a href="/vaults/main/-/tags/bar">bar</a>' in html


def test_tag_with_special_characters_round_trips(viewer, tag_pages):
    html = viewer.get("/vaults/main/tools/spaced").text
    for tag in ["machine learning", "c++ & co"]:
        href = re.search(
            rf'<a href="([^"]+)">{re.escape(tag.replace("&", "&amp;"))}</a>',
            html).group(1)
        assert href.startswith("/vaults/main/-/tags/")
        assert " " not in href
        resp = viewer.get(href)
        assert resp.status_code == 200
        assert page_links(resp.text) == ["/vaults/main/tools/spaced"]


def test_unused_tag_returns_200_with_no_page_links(viewer, tag_pages):
    resp = viewer.get("/vaults/main/-/tags/nosuchtag")
    assert resp.status_code == 200
    assert page_links(resp.text) == []
    assert 'rel="next"' not in resp.text


def test_tag_page_in_an_unserved_vault_returns_404(viewer, tag_pages):
    assert viewer.get("/vaults/team/-/tags/foo").status_code == 404


def test_tag_page_pages_through_50_at_a_time(viewer, many_pages):
    main = many_pages
    for n in range(60):
        (main / "tools" / f"t{n:02d}.md").write_text(
            tagged_page(f"Tagged {n:02d}", ["many"], f"2026-05-{n % 28 + 1:02d}"))
    first = viewer.get("/vaults/main/-/tags/many")
    assert len(page_links(first.text)) == 50
    assert 'rel="next" href="?page=2"' in first.text
    second = viewer.get("/vaults/main/-/tags/many?page=2")
    assert len(page_links(second.text)) == 10
    assert 'rel="prev" href="?page=1"' in second.text
    assert viewer.get("/vaults/main/-/tags/many?page=3").status_code == 404


def search_page(title, topic, body):
    return "\n".join(["---", f"title: {title}", f"topic: {topic}", "---", "",
                      f"# {title}", "", body, ""])


def checked_vaults(html):
    """The vault names whose search checkbox is checked."""
    return re.findall(
        r'<input type="checkbox" name="vault" value="([^"]+)" checked>', html)


def result_links(html):
    """The page links in the search results, in order."""
    return re.findall(r'<a class="result" href="([^"]+)"', html)


@pytest.fixture
def search_pages(catalog):
    """A page matching 'zebraquux' in each served vault."""
    main, work = catalog.vaults["main"], catalog.vaults["work"]
    (main / "research" / "mainpage.md").write_text(
        search_page("Main Zebra", "research", "zebraquux in main"))
    (work / "tools" / "workpage.md").write_text(
        search_page("Work Zebra", "tools", "zebraquux in work"))
    return catalog


def test_search_spans_all_vaults_with_every_checkbox_checked(viewer,
                                                            search_pages):
    resp = viewer.get("/search?q=zebraquux")
    assert resp.status_code == 200
    html = resp.text
    assert sorted(result_links(html)) == [
        "/vaults/main/research/mainpage", "/vaults/work/tools/workpage"]
    assert ">Main Zebra</a>" in html
    assert ">Work Zebra</a>" in html
    assert '<span class="vault">main</span>' in html
    assert '<span class="vault">work</span>' in html
    assert checked_vaults(html) == ["main", "work"]


def test_search_limited_to_the_named_vault(viewer, search_pages):
    html = viewer.get("/search?q=zebraquux&vault=main").text
    assert result_links(html) == ["/vaults/main/research/mainpage"]
    assert checked_vaults(html) == ["main"]
    assert 'name="vault" value="work">' in html


def test_search_ignores_unknown_vault_names(viewer, search_pages):
    html = viewer.get("/search?q=zebraquux&vault=main&vault=nosuch").text
    assert result_links(html) == ["/vaults/main/research/mainpage"]
    assert checked_vaults(html) == ["main"]


def test_search_with_only_unknown_vaults_searches_all(viewer, search_pages):
    html = viewer.get("/search?q=zebraquux&vault=nosuch&vault=team").text
    assert len(result_links(html)) == 2
    assert checked_vaults(html) == ["main", "work"]


def test_search_cuts_lines_to_200_characters(viewer, catalog):
    main = catalog.vaults["main"]
    (main / "research" / "long.md").write_text(
        search_page("Long", "research", "zebraquux " + "x" * 990))
    html = viewer.get("/search?q=zebraquux").text
    assert "x" * 190 in html
    assert "x" * 191 not in html


def test_search_shows_at_most_3_lines_per_result(viewer, catalog):
    main = catalog.vaults["main"]
    body = "\n\n".join(f"zebraquux line{n}" for n in range(5))
    (main / "research" / "many.md").write_text(
        search_page("Many", "research", body))
    html = viewer.get("/search?q=zebraquux").text
    assert "line2" in html
    assert "line3" not in html
    assert "line4" not in html


def test_search_caps_tiers_and_notes_truncation(viewer, catalog):
    main = catalog.vaults["main"]
    for n in range(25):
        (main / "research" / f"both{n:02d}.md").write_text(
            search_page(f"Both {n:02d}", "research", "zebraquux okapi"))
    for n in range(8):
        (main / "tools" / f"one{n:02d}.md").write_text(
            search_page(f"One {n:02d}", "tools", "zebraquux only"))
    html = viewer.get("/search?q=zebraquux+okapi").text
    assert len(result_links(html)) == 25
    assert "Partial matches (some terms only)" in html
    assert "Showing 25 of 33 matches" in html


def test_search_without_truncation_has_no_notice(viewer, search_pages):
    assert "Showing" not in viewer.get("/search?q=zebraquux").text


def test_empty_query_shows_the_form_only(viewer, search_pages):
    for url in ("/search", "/search?q=", "/search?q=+"):
        resp = viewer.get(url)
        assert resp.status_code == 200
        assert 'name="q"' in resp.text
        assert result_links(resp.text) == []
        assert "No results" not in resp.text
        assert checked_vaults(resp.text) == ["main", "work"]


def test_search_with_no_hits_says_so(viewer, search_pages):
    html = viewer.get("/search?q=nothingmatchesthis").text
    assert "No results found." in html


VAULT_VIEWS = ("/vaults/main/", "/vaults/main/research/",
               "/vaults/main/research/foo", "/vaults/main/-/tags/alpha")


def site_header(html):
    """The markup of the page's site header."""
    found = re.search(r'<header class="site-header">(.*?)</header>', html,
                      re.S)
    assert found, "no site header"
    return found.group(1)


def header_search_forms(header):
    """Each search form in the header, as its full markup."""
    return re.findall(r'<form[^>]*action="/search".*?</form>', header, re.S)


@pytest.mark.parametrize("url", ("/", "/search") + VAULT_VIEWS)
def test_header_has_the_mark_and_wordmark_linking_home(viewer, pages, url):
    header = site_header(viewer.get(url).text)
    brand = re.search(r'<a class="brand" href="/">(.*?)</a>', header, re.S)
    assert brand, url
    assert "<svg" in brand.group(1) and "</svg>" in brand.group(1)
    assert "Agent Wiki" in brand.group(1)


@pytest.mark.parametrize("url", ("/", "/search") + VAULT_VIEWS)
def test_header_has_the_theme_switch(viewer, pages, url):
    header = site_header(viewer.get(url).text)
    assert '<form class="theme" method="post" action="/theme"' in header


@pytest.mark.parametrize("url", VAULT_VIEWS)
def test_header_on_vault_views_links_the_vault_and_searches_it(viewer, pages,
                                                               url):
    header = site_header(viewer.get(url).text)
    assert ('<span class="vault-pick"><a href="/vaults/main/">main</a>'
            '</span>') in header
    forms = header_search_forms(header)
    assert len(forms) == 1
    assert 'method="get"' in forms[0] and 'name="q"' in forms[0]
    assert '<input type="hidden" name="vault" value="main">' in forms[0]


def test_header_vault_name_is_escaped_and_its_link_quoted(tmp_vault, tmp_path):
    name = "a&b c"
    registry = {name: VaultEntry(name=name, path=make_vault(tmp_path / "v"))}
    app = create_app(tmp_vault, SERVER_CONFIG,
                     viewer=build_vault_catalog(registry))
    client = TestClient(app, raise_server_exceptions=False)
    header = site_header(client.get("/vaults/a%26b%20c/").text)
    assert '<a href="/vaults/a%26b%20c/">a&amp;b c</a>' in header
    assert ('<input type="hidden" name="vault" value="a&amp;b c">'
            in header)


def test_header_on_the_vault_list_searches_all_vaults(viewer, pages):
    header = site_header(viewer.get("/").text)
    forms = header_search_forms(header)
    assert len(forms) == 1
    assert 'method="get"' in forms[0] and 'name="q"' in forms[0]
    assert 'name="vault"' not in forms[0]
    assert "vault-pick" not in header


@pytest.mark.parametrize("url", ["/search", "/search?q=foo"])
def test_header_on_the_search_page_has_no_search_form(viewer, pages, url):
    header = site_header(viewer.get(url).text)
    assert header_search_forms(header) == []
    assert 'role="search"' not in header
    assert "vault-pick" not in header


@pytest.fixture
def bad_page(tag_pages):
    """A page beside the tagged ones whose frontmatter is malformed YAML."""
    (tag_pages / "research" / "broken.md").write_text(
        "---\ntitle: [unclosed\ntags: [foo]\n---\nbody\n")
    return tag_pages


@pytest.mark.parametrize("url", [
    "/vaults/main/",
    "/vaults/main/research/",
    "/vaults/main/-/tags/foo",
    "/vaults/main/research/newer",
])
def test_unreadable_frontmatter_leaves_other_views_working(viewer, bad_page,
                                                            url):
    resp = viewer.get(url)
    assert resp.status_code == 200
    assert "/vaults/main/research/broken" not in resp.text


def test_page_with_unreadable_frontmatter_returns_404(viewer, bad_page):
    assert viewer.get("/vaults/main/research/broken").status_code == 404


@pytest.fixture
def loop_page(tag_pages):
    """A .md symlink beside the tagged pages that points to itself."""
    loop = tag_pages / "research" / "loop.md"
    loop.symlink_to(loop)
    return tag_pages


@pytest.mark.parametrize("url", [
    "/vaults/main/",
    "/vaults/main/research/",
    "/vaults/main/-/tags/foo",
    "/vaults/main/research/newer",
])
def test_symlink_loop_leaves_other_views_working(viewer, loop_page, url):
    resp = viewer.get(url)
    assert resp.status_code == 200
    assert "/vaults/main/research/loop" not in resp.text


def test_symlink_loop_page_returns_404(viewer, loop_page):
    assert viewer.get("/vaults/main/research/loop").status_code == 404


@pytest.fixture
def non_utf8_name(tag_pages):
    """A page beside the tagged ones whose file name is not UTF-8."""
    import os
    bad_name = os.fsencode(tag_pages / "research") + b"/caf\xe9.md"
    with open(bad_name, "wb") as f:
        f.write(tagged_page("Cafe", ["foo"], "2026-05-01").encode()
                + b"zebra cafe\n")
    (tag_pages / "research" / "good.md").write_text(
        tagged_page("Good", [], "2026-01-01") + "zebra good\n")
    return tag_pages


@pytest.mark.parametrize("url", [
    "/vaults/main/",
    "/vaults/main/research/",
    "/vaults/main/-/tags/foo",
    "/vaults/main/research/newer",
])
def test_non_utf8_name_leaves_other_views_working(viewer, non_utf8_name, url):
    resp = viewer.get(url)
    assert resp.status_code == 200
    assert "Cafe" not in resp.text


@pytest.mark.parametrize("ripgrep", [True, False])
def test_search_with_non_utf8_name_lists_good_page(viewer, non_utf8_name,
                                                   monkeypatch, ripgrep):
    import shutil
    if ripgrep and not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    if not ripgrep:
        monkeypatch.setattr("agent_wiki.search.shutil.which", lambda _: None)
    resp = viewer.get("/search?q=zebra&vault=main")
    assert resp.status_code == 200
    assert "/vaults/main/research/good" in resp.text


def test_search_lists_good_page_beside_unreadable_one(viewer, catalog):
    main = catalog.vaults["main"]
    (main / "research" / "good.md").write_text(
        tagged_page("Good", [], "2026-01-01") + "zebra good\n")
    (main / "research" / "broken.md").write_text(
        "---\ntitle: [unclosed\n---\nzebra here\n")
    html = viewer.get("/search?q=zebra&vault=main").text
    assert "/vaults/main/research/good" in html
    assert "Skipped vault" not in html


def test_search_lists_good_page_beside_undecodable_match(viewer, catalog):
    main = catalog.vaults["main"]
    (main / "research" / "good.md").write_text(
        tagged_page("Good", [], "2026-01-01") + "zebra good\n")
    (main / "research" / "latin.md").write_bytes(
        b"---\ntitle: Latin\n---\nzebra caf\xe9\n")
    html = viewer.get("/search?q=zebra&vault=main").text
    assert "/vaults/main/research/good" in html
    assert "Skipped vault" not in html
