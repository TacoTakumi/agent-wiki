"""The viewer's look: vendored stylesheets with their licence notices, inlined
into every page, with no JavaScript, no external CSS or fonts, and frontmatter
and query text escaped."""

import re
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.viewer.catalog import build_vault_catalog
from conftest import make_vault


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731, "tokens": []}

STATIC = files("agent_wiki.viewer") / "static"
STYLESHEETS = ["simple.css", "github-markdown.css", "viewer.css"]

# A url() whose target is a remote host: http(s) or protocol-relative.
REMOTE_URL = re.compile(r"""url\(\s*['"]?\s*(?:https?:)?//""", re.IGNORECASE)
STYLESHEET_LINK = re.compile(r"<link[^>]*stylesheet", re.IGNORECASE)

PAGE = """---
title: Foo Page
topic: research
tags: [alpha]
sources:
- raw/x.md
---

Body text here.

| a | b |
|---|---|
| 1 | 2 |
"""

TRICKY = """---
title: <b>x</b> page
topic: research
---

Tricky body.
"""


@pytest.fixture
def viewer(tmp_vault, tmp_path):
    main = make_vault(tmp_path / "main")
    (main / "research" / "foo.md").write_text(PAGE)
    (main / "research" / "tricky.md").write_text(TRICKY)
    catalog = build_vault_catalog(
        {"main": VaultEntry(name="main", path=main)})
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    return TestClient(app, raise_server_exceptions=False)


def css(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_vendored_stylesheets_ship_in_the_package():
    for name in STYLESHEETS:
        assert css(name).strip()


def test_licences_keep_both_mit_notices():
    notices = css("LICENSES.md")
    assert notices.count("MIT License") >= 2
    assert "Simple.css" in notices and "Kev Quirk" in notices
    assert "github-markdown-css" in notices and "Sindre Sorhus" in notices
    assert "Permission is hereby granted" in notices


def test_vendored_files_name_their_licence():
    assert "MIT" in css("simple.css").splitlines()[0]
    assert "MIT" in css("github-markdown.css").splitlines()[0]


def test_css_has_dark_mode_and_scrolling_tables():
    every = "".join(css(name) for name in STYLESHEETS)
    assert "prefers-color-scheme: dark" in every
    assert re.search(r"table[^{}]*\{[^}]*overflow-x:\s*auto", css("viewer.css"))


@pytest.mark.parametrize("name", STYLESHEETS)
def test_css_has_no_fonts_or_remote_urls(name):
    text = css(name)
    assert "@font-face" not in text
    assert "@import" not in text
    assert not REMOTE_URL.search(text)


URLS = [
    "/",
    "/vaults/main/",
    "/vaults/main/research/",
    "/vaults/main/research/foo",
    "/vaults/main/-/tags/alpha",
    "/search?q=body",
]


@pytest.mark.parametrize("url", URLS)
def test_every_page_inlines_its_css_and_loads_nothing(viewer, url):
    resp = viewer.get(url)
    assert resp.status_code == 200
    html = resp.text
    head = html[:html.index("</head>")]
    assert "<style>" in head
    assert ".markdown-body" in head
    assert "<script" not in html.lower()
    assert not STYLESHEET_LINK.search(html)
    assert "@font-face" not in html
    assert not REMOTE_URL.search(html)


def test_page_body_sits_inside_markdown_body(viewer):
    html = viewer.get("/vaults/main/research/foo").text
    start = html.index('<div class="markdown-body">')
    assert start < html.index("Body text here.") < html.index("<footer>")
    assert start < html.index("<table>") < html.index("<footer>")
    # The page header (title and metadata) stays outside the body wrapper.
    assert html.index("<h1>Foo Page</h1>") < start


def test_frontmatter_title_is_escaped(viewer):
    for url in ["/vaults/main/research/tricky", "/vaults/main/research/"]:
        html = viewer.get(url).text
        assert "&lt;b&gt;x&lt;/b&gt; page" in html
        assert "<b>x</b>" not in html


def test_search_query_is_escaped(viewer):
    resp = viewer.get("/search", params={"q": "<b>x</b>"})
    assert resp.status_code == 200
    assert "&lt;b&gt;x&lt;/b&gt;" in resp.text
    assert "<b>x</b>" not in resp.text
