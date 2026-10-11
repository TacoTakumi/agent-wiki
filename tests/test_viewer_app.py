"""Viewer routes mounted into the server app: switched on by a catalog, served
without a token, and absent (404) when the viewer is off."""

import re
import shutil
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


@pytest.fixture
def counted_viewer(tmp_vault, tmp_path):
    """Local vaults 'big' (1,001 pages over two topics), 'one' (1 page) and
    'empty' (0 pages), plus one remote and one missing vault."""
    big = make_vault(tmp_path / "big")
    for i in range(1000):
        (big / "research" / f"p{i}.md").write_text(
            tagged_page(f"P{i}", [], "2026-01-01"))
    (big / "tools" / "t.md").write_text(tagged_page("T", [], "2026-01-01"))
    one = make_vault(tmp_path / "one")
    (one / "research" / "a.md").write_text(tagged_page("A", [], "2026-01-01"))
    registry = {
        "big": VaultEntry(name="big", path=big),
        "one": VaultEntry(name="one", path=one),
        "empty": VaultEntry(name="empty", path=make_vault(tmp_path / "empty")),
        "team": VaultEntry(name="team", url="https://wiki.example.com",
                           token="tok-1"),
        "gone": VaultEntry(name="gone", path=tmp_path / "gone"),
    }
    app = create_app(tmp_vault, SERVER_CONFIG,
                     viewer=build_vault_catalog(registry))
    return TestClient(app, raise_server_exceptions=False)


def test_vault_list_has_a_page_title_and_subtitle(counted_viewer):
    html = counted_viewer.get("/").text
    assert re.search(r'<main[^>]*>\s*<h1 class="page-title">Vaults</h1>', html)
    assert ('<p class="page-sub">Local vaults served read-only on this '
            'machine.</p>') in html


def test_vault_list_is_one_row_box_with_page_counts(counted_viewer):
    html = counted_viewer.get("/").text
    boxes = re.findall(r'<div class="box[^"]*">\s*<ul class="rows">(.*?)</ul>',
                       html, re.S)
    assert len(boxes) == 1
    rows = re.findall(
        r'<li><a class="title" href="([^"]+)">([^<]+)</a>'
        r'<span class="meta">([^<]+)</span></li>', boxes[0])
    assert rows == [
        ("/vaults/big/", "big", "1,001 pages"),
        ("/vaults/empty/", "empty", "0 pages"),
        ("/vaults/one/", "one", "1 page"),
    ]


def test_vault_list_box_has_no_inline_style(counted_viewer):
    html = counted_viewer.get("/").text
    main = re.search(r"<main.*?</main>", html, re.S).group(0)
    assert "style=" not in main


@pytest.mark.parametrize("breakage", ["no_config", "bad_config", "removed"])
def test_vault_list_survives_a_vault_it_cannot_index(tmp_vault, tmp_path,
                                                    breakage):
    good = make_vault(tmp_path / "good")
    (good / "research" / "a.md").write_text(tagged_page("A", [], "2026-01-01"))
    broken = make_vault(tmp_path / "broken")
    registry = {
        "good": VaultEntry(name="good", path=good),
        "broken": VaultEntry(name="broken", path=broken),
    }
    catalog = build_vault_catalog(registry)
    if breakage == "no_config":
        (broken / "wiki.yaml").unlink()
    elif breakage == "bad_config":
        (broken / "wiki.yaml").write_text("topics: [unclosed\n")
    else:
        shutil.rmtree(broken)
    client = TestClient(create_app(tmp_vault, SERVER_CONFIG, viewer=catalog),
                        raise_server_exceptions=False)
    resp = client.get("/")
    assert resp.status_code == 200
    rows = re.findall(r'<li><a class="title" href="([^"]+)">([^<]+)</a>(.*?)</li>',
                      resp.text)
    assert rows == [
        ("/vaults/broken/", "broken", ""),
        ("/vaults/good/", "good", '<span class="meta">1 page</span>'),
    ]


def test_vault_list_keeps_unavailable_and_skipped_remote_note(counted_viewer):
    html = counted_viewer.get("/").text
    assert re.search(r"<h2[^>]*>Unavailable</h2>", html)
    assert "These local vaults have no directory on disk:" in html
    assert "gone (unavailable)" in html
    assert ("1 remote vault skipped: the viewer serves local vaults only."
            in html)
    assert "/vaults/team/" not in html
    assert "/vaults/gone/" not in html


def test_vault_list_without_local_vaults_says_so(tmp_vault, tmp_path):
    registry = {"gone": VaultEntry(name="gone", path=tmp_path / "gone")}
    app = create_app(tmp_vault, SERVER_CONFIG,
                     viewer=build_vault_catalog(registry))
    html = TestClient(app).get("/").text
    assert "No local vaults to serve." in html
    assert '<ul class="rows">' not in html


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


def page_part(html, pattern):
    """The inner markup of the first match of `pattern` (one group)."""
    found = re.search(pattern, html, re.S)
    assert found, f"no match for {pattern}"
    return found.group(1)


def side_section(html, heading):
    """A page view sidebar section's markup under its h2 heading; `html` is
    one aside's inner markup, so a section ends where the next one starts or
    at the end."""
    return page_part(html, r'<div class="side-section">\s*<h2>'
                     + re.escape(heading) + r'</h2>(.*?)</div>\s*'
                     r'(?=<div class="side-section">|\Z)')


def test_page_view_lays_out_path_box_about_tags_and_sources(viewer, pages):
    html = viewer.get("/vaults/main/research/foo").text
    # The title once, with the Updated line under it.
    assert re.search(r'<h1 class="page-title">Foo Page</h1>\s*'
                     r'<p class="page-sub">Updated 2026-03-04</p>', html)
    assert len(re.findall(r"<h1[\s>]", html)) == 1
    layout = page_part(html, r'<div class="page-layout">(.*)</main>')
    # The body sits in a box headed by the vault-relative path.
    article = page_part(layout, r'<article class="box">(.*?)</article>')
    assert page_part(article, r'<div class="box-head">'
                     r'<span class="file-head">([^<]*)</span></div>') \
        == "research/foo.md"
    body = article.index('<div class="markdown-body body-pad">')
    assert body < article.index("<table>")
    # About and Tags in the meta area.
    meta = page_part(layout, r'<aside class="meta">(.*?)</aside>')
    about = page_part(side_section(meta, "About"), r"<dl>(.*?)</dl>")
    assert re.findall(r"<div><dt>([^<]*)</dt><dd>(.*?)</dd></div>",
                      about) == [
        ("Topic", '<a href="/vaults/main/research/">research</a>'),
        ("Created", "2026-01-02"),
        ("Updated", "2026-03-04"),
    ]
    tags = page_part(side_section(meta, "Tags"),
                     r'<ul class="tags">(.*?)</ul>')
    assert re.findall(r'<li><a href="([^"]*)">([^<]*)</a></li>', tags) == [
        ("/vaults/main/-/tags/alpha", "alpha"),
        ("/vaults/main/-/tags/beta", "beta"),
    ]
    # Sources in their own area: web sources as links, raw paths as text.
    src = page_part(layout, r'<aside class="src">(.*?)</aside>')
    sources = page_part(side_section(src, "Sources"),
                        r'<ul class="sources">(.*?)</ul>')
    assert re.findall(r"<li>(.*?)</li>", sources) == [
        "raw/x.md",
        '<a href="https://example.com/a">https://example.com/a</a>',
    ]
    assert layout.index("</article>") < layout.index('<aside class="meta">') \
        < layout.index('<aside class="src">')


def test_page_view_without_dates_tags_or_sources_leaves_them_out(viewer,
                                                                pages):
    html = viewer.get("/vaults/main/research/sub/bar").text
    assert 'class="page-sub"' not in html
    assert page_part(html, r'<span class="file-head">([^<]*)</span>') \
        == "research/sub/bar.md"
    meta = page_part(html, r'<aside class="meta">(.*?)</aside>')
    about = page_part(side_section(meta, "About"), r"<dl>(.*?)</dl>")
    assert re.findall(r"<dt>([^<]*)</dt>", about) == ["Topic"]
    assert "<h2>Tags</h2>" not in html
    assert '<aside class="src">' not in html


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


def test_vault_home_lists_the_20_most_recent_pages(viewer, home_pages):
    links = page_links(viewer.get("/vaults/main/").text)
    expected = []
    for day in range(25, 5, -1):
        topic = "research" if day % 5 else "tools"
        expected.append(f"/vaults/main/{topic}/p{day:02d}")
    assert links == expected


def test_vault_home_has_a_search_form_for_this_vault(viewer, home_pages):
    side = sidebar(viewer.get("/vaults/main/").text)
    form = re.search(r'<form class="side-search" method="get" '
                     r'action="/search" role="search">(.*?)</form>', side,
                     re.S)
    assert form, "no sidebar search form"
    assert 'name="q"' in form.group(1)
    assert ('<input type="hidden" name="vault" value="main">'
            in form.group(1))


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
        r'<input type="checkbox" form="search-form" name="vault" '
        r'value="([^"]+)" checked>', html)


RESULT_BOX = re.compile(
    r'<div class="box result"><div class="box-head">'
    r'<span class="vault-label">(.*?)</span><a href="([^"]+)">(.*?)</a>'
    r'(?:<span class="terms">(.*?)</span>)?</div>'
    r'<ul class="snippets">(.*?)</ul></div>', re.S)


def result_boxes(html):
    """Each search result box as (vault label, link, title markup, terms
    note or None, [snippet line markup]), in order."""
    return [(vault, url, title, terms or None,
             re.findall(r"<li>(.*?)</li>", snippets, re.S))
            for vault, url, title, terms, snippets in RESULT_BOX.findall(html)]


def result_links(html):
    """The page links in the search results, in order."""
    return [box[1] for box in result_boxes(html)]


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
    assert sorted(box[0] for box in result_boxes(html)) == ["main", "work"]
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
    assert '<h2 class="tier">Partial matches</h2>' in html
    assert '<p class="notice">25 pages match every term</p>' in html
    assert "Showing 25 of 33 matches" in html


@pytest.fixture
def unserved_hits(catalog):
    """Matches for 'zebraquux' in main: a research page, a note in journals/
    (not a wiki.yaml topic) and a research file with no frontmatter."""
    main = catalog.vaults["main"]
    (main / "research" / "served.md").write_text(
        search_page("Served Zebra", "research", "zebraquux served"))
    (main / "journals").mkdir()
    (main / "journals" / "note.md").write_text(
        search_page("Journal Zebra", "journals", "zebraquux journal"))
    (main / "research" / "nofm.md").write_text(
        "# No frontmatter\n\nzebraquux bare\n")
    return catalog


def test_search_shows_only_hits_the_page_view_serves(viewer, unserved_hits):
    html = viewer.get("/search?q=zebraquux").text
    assert result_links(html) == ["/vaults/main/research/served"]
    assert "Journal Zebra" not in html
    assert "/vaults/main/journals/note" not in html
    assert "/vaults/main/research/nofm" not in html
    assert "zebraquux bare" not in html
    for link in result_links(html):
        assert viewer.get(link).status_code == 200


def test_search_left_out_hits_do_not_change_the_cli_search(unserved_hits,
                                                         monkeypatch):
    monkeypatch.setenv("AWIKI_VAULT", str(unserved_hits.vaults["main"]))
    out = CliRunner().invoke(cli, ["search", "zebraquux"]).output
    assert "research/served.md" in out
    assert "journals/note.md" in out
    assert "research/nofm.md" in out


def test_search_left_out_hits_do_not_crowd_out_served_ones(viewer, catalog):
    """Unserved notes that rank above every served page still leave the
    served pages in the shown tiers, and the count line counts served hits
    only."""
    main = catalog.vaults["main"]
    (main / "journals").mkdir()
    busy = "\n\n".join(["zebraquux okapi"] * 5)
    for n in range(30):
        (main / "journals" / f"j{n:02d}.md").write_text(
            search_page(f"Journal {n:02d}", "journals", busy))
    for n in range(8):
        (main / "research" / f"bare{n:02d}.md").write_text(
            f"# Bare {n:02d}\n\nzebraquux\n")
    for n in range(25):
        (main / "research" / f"both{n:02d}.md").write_text(
            search_page(f"Both {n:02d}", "research", "zebraquux okapi"))
    for n in range(7):
        (main / "tools" / f"one{n:02d}.md").write_text(
            search_page(f"One {n:02d}", "tools", "zebraquux only"))
    html = viewer.get("/search?q=zebraquux+okapi").text
    links = result_links(html)
    assert len(links) == 25
    assert all("/journals/" not in link and "/bare" not in link
               for link in links)
    assert sum("/tools/one" in link for link in links) == 5
    assert sum("/research/both" in link for link in links) == 20
    assert "Showing 25 of 32 matches" in html


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


@pytest.fixture
def tier_hits(catalog):
    """For 'zebraquux okapi': a page in main with both terms and a page in
    work with only the first."""
    main, work = catalog.vaults["main"], catalog.vaults["work"]
    (main / "research" / "full.md").write_text(
        search_page("Zebraquux Okapi Guide", "research",
                    "zebraquux meets okapi"))
    (work / "tools" / "part.md").write_text(
        search_page("Zebraquux Notes", "tools", "zebraquux alone"))
    return catalog


def search_layout(html):
    """The search page's main column and sidebar markup."""
    found = re.search(r'<div class="layout">\s*<div>(.*?)</div>\s*'
                      r'<aside>(.*?)</aside>\s*</div>', html, re.S)
    assert found, "no search layout"
    return found.group(1), found.group(2)


def test_search_page_shows_the_query_box_in_the_main_column(viewer,
                                                           tier_hits):
    html = viewer.get("/search?q=zebraquux+okapi").text
    assert '<h1 class="page-title">Search</h1>' in html
    column, _ = search_layout(html)
    form = re.search(r'<form id="search-form" class="search-row" '
                     r'method="get" action="/search" role="search">(.*?)'
                     r'</form>', column, re.S)
    assert form, "no search-row form"
    assert ('<input type="search" name="q" value="zebraquux okapi" '
            'aria-label="Search">') in form.group(1)
    assert '<button class="btn btn-accent" type="submit">Search</button>' \
        in form.group(1)


def test_search_page_counts_the_all_terms_tier(viewer, tier_hits,
                                               search_pages):
    one = viewer.get("/search?q=zebraquux+okapi").text
    assert '<p class="notice">1 page matches every term</p>' in one
    # search_pages adds one page per vault holding zebraquux alone.
    many = viewer.get("/search?q=zebraquux").text
    assert '<p class="notice">4 pages match every term</p>' in many


def test_search_page_counts_no_all_terms_hit(viewer, tier_hits):
    html = viewer.get("/search?q=zebraquux+nothingelse").text
    assert '<p class="notice">No page matches every term</p>' in html
    assert len(result_boxes(html)) == 2


def test_search_page_lays_out_a_box_per_hit(viewer, tier_hits):
    html = viewer.get("/search?q=zebraquux+okapi").text
    column, _ = search_layout(html)
    boxes = result_boxes(column)
    assert [(b[0], b[1]) for b in boxes] == [
        ("main", "/vaults/main/research/full"),
        ("work", "/vaults/work/tools/part")]
    vault, url, title, terms, lines = boxes[0]
    assert title == "<mark>Zebraquux</mark> <mark>Okapi</mark> Guide"
    assert terms is None
    assert "<mark>zebraquux</mark> meets <mark>okapi</mark>" in lines
    assert boxes[1][2] == "<mark>Zebraquux</mark> Notes"
    assert "<mark>zebraquux</mark> alone" in boxes[1][4]


def test_search_page_shows_the_partial_tier_with_term_counts(viewer,
                                                             tier_hits):
    html = viewer.get("/search?q=zebraquux+okapi").text
    column, _ = search_layout(html)
    heading = column.index('<h2 class="tier">Partial matches</h2>')
    full = column.index('href="/vaults/main/research/full"')
    part = column.index('href="/vaults/work/tools/part"')
    assert full < heading < part
    terms = [box[3] for box in result_boxes(column)]
    assert terms == [None, "1 of 2 terms"]


def test_search_page_escapes_html_around_marks(viewer, catalog):
    main = catalog.vaults["main"]
    (main / "research" / "amp.md").write_text(
        search_page("Ampersand <b>Page</b>", "research",
                    "x <i>tag</i> a&b y"))
    html = viewer.get("/search?q=a%26b").text
    boxes = result_boxes(html)
    assert len(boxes) == 1
    assert boxes[0][2] == "Ampersand &lt;b&gt;Page&lt;/b&gt;"
    assert "x &lt;i&gt;tag&lt;/i&gt; <mark>a&amp;b</mark> y" in boxes[0][4]
    assert "<i>tag</i>" not in html
    assert "<b>Page</b>" not in html


def test_search_page_puts_the_vault_checkboxes_in_the_sidebar(viewer,
                                                             tier_hits):
    html = viewer.get("/search?q=zebraquux+okapi&vault=main").text
    column, side = search_layout(html)
    assert 'type="checkbox"' not in column
    filters = re.search(r'<div class="side-section filters">\s*'
                        r'<h2>Vaults</h2>(.*?)</div>', side, re.S)
    assert filters, "no vault filters in the sidebar"
    assert re.findall(r'<label><input type="checkbox" form="search-form" '
                      r'name="vault" value="([^"]+)"( checked)?> \1</label>',
                      filters.group(1)) == [("main", " checked"),
                                            ("work", "")]
    # The checkboxes submit with the query box's form.
    assert html.count('id="search-form"') == 1


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


MARK_PATHS = ('d="M10 2H5.5v18H10M20 2h4.5v18H20"', 'd="M11.5 8h7M11.5 14h7"')


@pytest.mark.parametrize("url", ("/", "/search") + VAULT_VIEWS)
def test_header_brand_holds_the_bracketed_page_mark(viewer, pages, url):
    header = site_header(viewer.get(url).text)
    brand = re.search(r'<a class="brand" href="/">(.*?)</a>', header, re.S).group(1)
    for path in MARK_PATHS:
        assert path in brand, url
    assert "M7 2H3v18h4" not in brand


FAVICONS = {
    "/favicon.svg": "image/svg+xml",
    "/favicon.ico": "image/vnd.microsoft.icon",
    "/apple-touch-icon.png": "image/png",
}


@pytest.mark.parametrize("url", FAVICONS)
def test_each_favicon_is_served_without_a_token(viewer, url):
    resp = viewer.get(url)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(FAVICONS[url])


def test_the_favicon_files_hold_the_sizes_browsers_ask_for(viewer):
    ico = viewer.get("/favicon.ico").content
    count = int.from_bytes(ico[4:6], "little")
    png = viewer.get("/apple-touch-icon.png").content
    assert sorted(ico[6 + 16 * i] for i in range(count)) == [16, 32, 48]
    assert int.from_bytes(png[16:20], "big") == int.from_bytes(png[20:24], "big") == 180


@pytest.mark.parametrize("url", ("/", "/search") + VAULT_VIEWS)
def test_every_page_links_the_favicons(viewer, pages, url):
    html = viewer.get(url).text
    assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in html
    assert '<link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48">' in html
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in html


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


@pytest.fixture
def nav_viewer(tmp_vault, tmp_path):
    """A client over one vault 'main' whose topics are research (2 pages)
    and tools (1 page), in that order."""
    main = make_vault(tmp_path / "navmain")
    (main / "wiki.yaml").write_text(
        "vault: {name: Nav, version: 1}\n"
        "topics: [research, tools]\ndefault_topic: research\n")
    (main / "research" / "foo.md").write_text(
        tagged_page("Foo", ["alpha"], "2026-01-01"))
    (main / "research" / "bar.md").write_text(
        tagged_page("Bar", [], "2026-01-02"))
    (main / "tools" / "hammer.md").write_text(
        tagged_page("Hammer", ["alpha"], "2026-01-03"))
    registry = {"main": VaultEntry(name="main", path=main)}
    app = create_app(tmp_vault, SERVER_CONFIG,
                     viewer=build_vault_catalog(registry))
    return TestClient(app, raise_server_exceptions=False)


def topic_tabs(html):
    """The topic tabs as (href, label, count, is_current), in order; count is
    None for a tab without a count."""
    row = re.search(r'<nav class="tabs" aria-label="Topics">(.*?)</nav>',
                    html, re.S)
    assert row, "no tab row"
    tabs = []
    for href, attrs, inner in re.findall(
            r'<a href="([^"]*)"([^>]*)>(.*?)</a>', row.group(1), re.S):
        count = re.search(r'<span class="count">([^<]*)</span>', inner)
        label = re.sub(r"<span.*?</span>", "", inner, flags=re.S).strip()
        tabs.append((href, label, count.group(1) if count else None,
                     'aria-current="page"' in attrs))
    return tabs


def breadcrumb(html):
    """The breadcrumb parts as (text, href or None, is_current), in order."""
    nav = re.search(r'<nav class="crumbs" aria-label="Breadcrumb">(.*?)</nav>',
                    html, re.S)
    assert nav, "no breadcrumb"
    parts = []
    for tag, attrs, text in re.findall(r"<(a|span)([^>]*)>(.*?)</\1>",
                                       nav.group(1), re.S):
        if 'class="sep"' in attrs:
            continue
        href = re.search(r'href="([^"]*)"', attrs)
        parts.append((text.strip(), href.group(1) if href else None,
                      'aria-current="page"' in attrs))
    return parts


def test_tabs_on_vault_home_read_overview_then_topics_with_counts(nav_viewer):
    assert topic_tabs(nav_viewer.get("/vaults/main/").text) == [
        ("/vaults/main/", "Overview", None, True),
        ("/vaults/main/research/", "research", "2", False),
        ("/vaults/main/tools/", "tools", "1", False),
    ]


@pytest.mark.parametrize("url, current", [
    ("/vaults/main/", "Overview"),
    ("/vaults/main/research/", "research"),
    ("/vaults/main/tools/", "tools"),
    ("/vaults/main/research/foo", "research"),
    ("/vaults/main/tools/hammer", "tools"),
    ("/vaults/main/-/tags/alpha", None),
])
def test_tabs_mark_the_current_tab(nav_viewer, url, current):
    tabs = topic_tabs(nav_viewer.get(url).text)
    assert [label for _, label, _, _ in tabs] == [
        "Overview", "research", "tools"]
    marked = [label for _, label, _, is_current in tabs if is_current]
    assert marked == ([current] if current else [])


def test_tabs_sit_in_the_header_below_the_top_row(nav_viewer):
    header = site_header(nav_viewer.get("/vaults/main/").text)
    assert '<div class="container tabs-row">' in header
    assert header.index('class="theme"') < header.index('class="tabs"')


def test_tabs_counts_match_the_topic_listings(nav_viewer):
    for href, _, count, _ in topic_tabs(nav_viewer.get("/vaults/main/").text):
        if count is not None:
            listing = nav_viewer.get(href).text
            assert len(page_links(listing)) == int(count), href


@pytest.mark.parametrize("url", ["/", "/search", "/search?q=foo"])
def test_no_tabs_on_the_vault_list_or_search(nav_viewer, url):
    html = nav_viewer.get(url).text
    # The inlined stylesheet names the classes, so look for the markup.
    assert '<nav class="tabs"' not in html
    assert 'class="container tabs-row"' not in html


def test_tabs_and_breadcrumb_quote_names(tmp_vault, tmp_path):
    vault = make_vault(tmp_path / "q")
    (vault / "wiki.yaml").write_text(
        "vault: {name: Q, version: 1}\ntopics: ['my notes']\n")
    (vault / "my notes").mkdir()
    (vault / "my notes" / "p.md").write_text(
        tagged_page("P", ["x"], "2026-01-01"))
    name = "a&b c"
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=build_vault_catalog(
        {name: VaultEntry(name=name, path=vault)}))
    client = TestClient(app, raise_server_exceptions=False)
    html = client.get("/vaults/a%26b%20c/my%20notes/p").text
    assert topic_tabs(html) == [
        ("/vaults/a%26b%20c/", "Overview", None, False),
        ("/vaults/a%26b%20c/my%20notes/", "my notes", "1", True),
    ]
    assert breadcrumb(html) == [
        ("a&amp;b c", "/vaults/a%26b%20c/", False),
        ("my notes", "/vaults/a%26b%20c/my%20notes/", False),
    ]


def test_breadcrumb_on_vault_home(nav_viewer):
    assert breadcrumb(nav_viewer.get("/vaults/main/").text) == [
        ("Vaults", "/", False),
        ("main", None, True),
    ]


def recent_box(html):
    """The Recently updated box's rows as (href, title, topic, date)."""
    box = re.search(r'<div class="box">\s*<div class="box-head">Recently '
                    r'updated</div>\s*<ul class="rows">(.*?)</ul>', html,
                    re.S)
    assert box, "no Recently updated box"
    return re.findall(r'<li><a class="title" href="([^"]*)">([^<]*)</a>'
                      r'<span class="topic-tag">([^<]*)</span>'
                      r'<time>([^<]*)</time></li>', box.group(1))


def sidebar(html):
    """The vault home's sidebar markup, beside the main column."""
    side = re.search(r'<div class="layout">.*?<aside>(.*?)</aside>', html,
                     re.S)
    assert side, "no sidebar in a .layout grid"
    return side.group(1)


def about_block(html):
    """The sidebar About block as {term: value}."""
    about = re.search(r'<div class="side-section">\s*<h2>About</h2>\s*'
                      r'<dl>(.*?)</dl>', sidebar(html), re.S)
    assert about, "no About block"
    return dict(re.findall(r"<dt>([^<]*)</dt>\s*<dd>([^<]*)</dd>",
                           about.group(1)))


def test_vault_home_lists_recently_updated_rows_newest_first(nav_viewer):
    assert recent_box(nav_viewer.get("/vaults/main/").text) == [
        ("/vaults/main/tools/hammer", "Hammer", "tools", "2026-01-03"),
        ("/vaults/main/research/bar", "Bar", "research", "2026-01-02"),
        ("/vaults/main/research/foo", "Foo", "research", "2026-01-01"),
    ]


def test_vault_home_about_block_counts_pages_topics_and_newest_date(
        nav_viewer):
    assert about_block(nav_viewer.get("/vaults/main/").text) == {
        "Pages": "3", "Topics": "2", "Updated": "2026-01-03"}


def test_vault_home_topic_count_includes_empty_topics_like_the_tabs(viewer):
    # make_vault lists four topics, all present as (empty) folders; the tab
    # row shows all four, so About counts four too.
    html = viewer.get("/vaults/main/").text
    assert about_block(html) == {"Pages": "0", "Topics": "4"}
    assert "No pages yet." in html


def test_vault_home_has_no_topics_list_outside_the_tab_row(nav_viewer):
    html = nav_viewer.get("/vaults/main/").text
    rest = re.sub(r'<nav class="tabs" aria-label="Topics">.*?</nav>', "",
                  html, flags=re.S)
    assert '<nav class="tabs"' in html
    assert 'href="/vaults/main/research/"' not in rest
    assert 'href="/vaults/main/tools/"' not in rest
    assert '<ul class="topics">' not in rest


def test_breadcrumb_on_topic_listing(nav_viewer):
    assert breadcrumb(nav_viewer.get("/vaults/main/research/").text) == [
        ("main", "/vaults/main/", False),
        ("research", None, True),
    ]


def test_breadcrumb_on_tag_page(nav_viewer):
    # There is no tags index view, so 'tags' is plain text.
    assert breadcrumb(nav_viewer.get("/vaults/main/-/tags/alpha").text) == [
        ("main", "/vaults/main/", False),
        ("tags", None, False),
        ("alpha", None, True),
    ]


def test_breadcrumb_on_page_view_links_both_parts(nav_viewer):
    html = nav_viewer.get("/vaults/main/tools/hammer").text
    assert breadcrumb(html) == [
        ("main", "/vaults/main/", False),
        ("tools", "/vaults/main/tools/", False),
    ]
    assert html.index('class="crumbs"') < html.index(
        '<h1 class="page-title">Hammer</h1>')


@pytest.mark.parametrize("url", ["/", "/search"])
def test_no_breadcrumb_on_the_vault_list_or_search(nav_viewer, url):
    assert 'class="crumbs"' not in nav_viewer.get(url).text


def listing_box(html):
    """A topic or tag listing's row box, as its markup."""
    box = re.search(r'<div class="box">(.*?)</div>', html, re.S)
    assert box, "no row box"
    return box.group(1)


def listing_rows(html):
    """A listing's rows as (href, title, date), in order."""
    rows = re.search(r'<ul class="rows">(.*?)</ul>', listing_box(html), re.S)
    assert rows, "no rows in the box"
    return re.findall(r'<li><a class="title" href="([^"]*)">([^<]*)</a>'
                      r'<time>([^<]*)</time></li>', rows.group(1))


def test_topic_listing_lays_out_a_row_box_newest_first(nav_viewer):
    assert listing_rows(nav_viewer.get("/vaults/main/research/").text) == [
        ("/vaults/main/research/bar", "Bar", "2026-01-02"),
        ("/vaults/main/research/foo", "Foo", "2026-01-01"),
    ]


def test_tag_listing_lays_out_a_row_box_newest_first(nav_viewer):
    assert listing_rows(nav_viewer.get("/vaults/main/-/tags/alpha").text) == [
        ("/vaults/main/tools/hammer", "Hammer", "2026-01-03"),
        ("/vaults/main/research/foo", "Foo", "2026-01-01"),
    ]


def test_topic_listing_subtitle_counts_pages_newest_first(nav_viewer):
    html = nav_viewer.get("/vaults/main/research/").text
    assert '<p class="page-sub">2 pages, newest first</p>' in html
    html = nav_viewer.get("/vaults/main/tools/").text
    assert '<p class="page-sub">1 page, newest first</p>' in html


def test_tag_listing_subtitle_names_the_tag(nav_viewer):
    html = nav_viewer.get("/vaults/main/-/tags/alpha").text
    assert '<p class="page-sub">Pages tagged alpha, newest first</p>' in html


@pytest.mark.parametrize("url", ["/vaults/main/research/",
                                 "/vaults/main/-/tags/alpha"])
def test_listing_has_no_old_list_markup(nav_viewer, url):
    html = nav_viewer.get(url).text
    assert '<ul class="pages">' not in html
    assert "<small>" not in html


def test_topic_listing_pager_sits_under_the_box_on_51_pages(viewer, catalog):
    main = catalog.vaults["main"]
    for n in range(51):
        (main / "research" / f"p{n:02d}.md").write_text(dated_page(
            f"Page {n:02d}", "research",
            updated=f"2026-{n // 28 + 1:02d}-{n % 28 + 1:02d}"))
    html = viewer.get("/vaults/main/research/").text
    rows = listing_rows(html)
    assert len(rows) == 50
    assert rows[0][0] == "/vaults/main/research/p50"
    pager = re.search(r'<nav class="pager" aria-label="Pages">(.*?)</nav>',
                      html, re.S)
    assert pager, "no pager"
    assert "<span>Page 1 of 2</span>" in pager.group(1)
    assert 'rel="next" href="?page=2"' in pager.group(1)
    assert html.index('<div class="box">') < pager.start()
    assert re.search(r"</ul>\s*</div>", html).end() < pager.start()


def test_topic_listing_without_a_pager_under_51_pages(nav_viewer):
    assert 'class="pager"' not in nav_viewer.get("/vaults/main/research/").text


def test_empty_topic_listing_keeps_its_text_in_the_box(viewer):
    box = listing_box(viewer.get("/vaults/main/projects/").text)
    assert "No pages in this topic." in box
    assert '<ul class="rows">' not in box


def test_empty_tag_listing_keeps_its_text_in_the_box(viewer):
    box = listing_box(viewer.get("/vaults/main/-/tags/nosuchtag").text)
    assert "No pages have this tag." in box
    assert '<ul class="rows">' not in box
