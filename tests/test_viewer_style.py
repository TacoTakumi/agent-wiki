"""The viewer's look: its own token-based frame stylesheet plus the vendored
github-markdown-css (with its licence notice), inlined into every page, with no
JavaScript, no external CSS or fonts, and frontmatter and query text escaped.

The frame stylesheet is checked structurally: every colour is a light-dark()
token on :root, the text colours meet WCAG AA in both themes, and the layout
rules (container, margins, sidebar grid) are present."""

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
STYLESHEETS = ["github-markdown.css", "viewer.css"]

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


def test_licences_keep_the_mit_notice():
    notices = css("LICENSES.md")
    assert "MIT License" in notices
    assert "github-markdown-css" in notices and "Sindre Sorhus" in notices
    assert "Permission is hereby granted" in notices


def test_vendored_files_name_their_licence():
    assert "MIT" in css("github-markdown.css").splitlines()[0]


def test_css_has_dark_mode_and_scrolling_tables():
    viewer_css = css("viewer.css")
    assert re.search(r"color-scheme:\s*light dark", viewer_css)
    assert "light-dark(" in viewer_css
    assert re.search(r"table[^{}]*\{[^}]*overflow-x:\s*auto", viewer_css)


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


# Text only Simple.css carries: its name in the header comment and the
# :root variable names it defines.
SIMPLE_CSS_MARKERS = ["Simple.css", "simple.css", "--accent-bg", "--text-light",
                      "--standard-border-radius", "--accent-hover"]


def test_simple_css_is_gone_from_the_package():
    assert not (STATIC / "simple.css").is_file()
    notices = css("LICENSES.md")
    assert "Simple.css" not in notices
    assert "simple.css" not in notices
    assert "Kev Quirk" not in notices


def test_nothing_in_the_viewer_loads_simple_css():
    viewer_dir = files("agent_wiki.viewer")
    sources = [viewer_dir / "routes.py", viewer_dir / "render.py",
               viewer_dir / "catalog.py"]
    sources += [entry for entry in (viewer_dir / "templates").iterdir()
                if entry.name.endswith(".html")]
    sources += [STATIC / name for name in STYLESHEETS]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for marker in SIMPLE_CSS_MARKERS:
            assert marker not in text, (source.name, marker)


@pytest.mark.parametrize("url", URLS)
def test_no_page_carries_simple_css(viewer, url):
    html = viewer.get(url).text
    for marker in SIMPLE_CSS_MARKERS:
        assert marker not in html, marker


# ---------- Structural checks on the frame stylesheet ----------

COLOUR_LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\bhwb\(|\blab\(|\blch\(|\boklch\(")
HEX = r"#[0-9a-fA-F]{6}"
PAIR = re.compile(rf"light-dark\(\s*({HEX})\s*,\s*({HEX})\s*\)")

ACCENT_LIGHT = "#5b4fd6"
ACCENT_DARK = "#a99fff"


def css_rules(text: str) -> list[tuple[str | None, str, str]]:
    """Split a stylesheet into (media query or None, selector, body) rules.
    Comments are dropped; one level of @media nesting is enough here."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    rules = []
    media = None
    start = i = 0
    while i < len(text):
        if text[i] == "{":
            head = " ".join(text[start:i].split())
            if head.startswith("@media"):
                media = re.sub(r"\s*:\s*", ": ", head[len("@media"):].strip())
            else:
                end = text.index("}", i)
                rules.append((media, head, text[i + 1:end]))
                i = end
            start = i + 1
        elif text[i] == "}":
            # Only a media block's closing brace reaches here.
            media = None
            start = i + 1
        i += 1
    return rules


def declarations(body: str) -> dict[str, str]:
    out = {}
    for part in body.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            value = re.sub(r"\s*,\s*", ", ", " ".join(value.split()))
            out[name.strip()] = value
    return out


def frame_rules():
    return css_rules(css("viewer.css"))


def token_block() -> str:
    """The one :root rule outside any media query that holds the colour
    tokens."""
    blocks = [body for media, selector, body in frame_rules()
              if media is None and selector == ":root" and "light-dark(" in body]
    assert len(blocks) == 1
    return blocks[0]


def tokens() -> dict[str, tuple[str, str]]:
    """Colour token name -> (light, dark) hex pair."""
    out = {}
    for name, value in declarations(token_block()).items():
        if name.startswith("--"):
            match = PAIR.fullmatch(value)
            assert match, (name, value)
            out[name] = (match.group(1).lower(), match.group(2).lower())
    return out


def test_token_block_sets_both_colour_schemes():
    decls = declarations(token_block())
    assert decls.get("color-scheme") == "light dark"


def test_every_token_is_a_light_dark_pair():
    found = tokens()
    for name in ["--bg", "--bg-muted", "--fg", "--fg-muted", "--accent",
                 "--border"]:
        assert name in found, name


def test_colour_literals_sit_only_in_the_token_block():
    block = token_block()
    for media, selector, body in frame_rules():
        if body == block:
            continue
        assert not COLOUR_LITERAL.search(body), (media, selector, body)


def test_accent_is_one_indigo_pair():
    assert tokens()["--accent"] == (ACCENT_LIGHT, ACCENT_DARK)
    text = css("viewer.css").lower()
    assert text.count(ACCENT_LIGHT) == 1
    assert text.count(ACCENT_DARK) == 1


def test_accent_tints_derive_from_the_accent_token():
    block = token_block()
    for media, selector, body in frame_rules():
        if body == block:
            continue
        for name, value in declarations(body).items():
            # A plain alias of the accent is fine; a tint must be mixed.
            if (name.startswith("--") and "var(--accent" in value
                    and value != "var(--accent)"):
                assert value.startswith("color-mix("), (selector, name, value)


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    light, dark = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_contrast_helper_matches_known_values():
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast("#777777", "#ffffff"), 2) == 4.48


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
@pytest.mark.parametrize("background", ["--bg", "--bg-muted"])
@pytest.mark.parametrize("text", ["--fg", "--fg-muted", "--accent"])
def test_text_colours_meet_wcag_aa(text, background, theme):
    found = tokens()
    ratio = contrast(found[text][theme], found[background][theme])
    assert ratio >= 4.5, (text, background, theme, round(ratio, 2))


def _rule(selector: str, media: str | None = None) -> dict[str, str]:
    merged = {}
    for rule_media, rule_selector, body in frame_rules():
        if rule_media == media and selector in [s.strip() for s in rule_selector.split(",")]:
            merged.update(declarations(body))
    return merged


def test_container_is_at_most_1280px_with_gutter_margins():
    container = _rule(".container")
    assert container.get("max-width") == "1280px"
    assert "var(--gutter)" in container.get("padding", "")


def test_side_margins_shrink_at_the_breakpoints():
    root = {}
    for media, selector, body in frame_rules():
        if media is None and selector == ":root":
            root.update(declarations(body))
    assert root.get("--gutter") == "32px"
    assert _rule(":root", "(max-width: 1200px)").get("--gutter") == "16px"
    assert _rule(":root", "(max-width: 767px)").get("--gutter") == "8px"


@pytest.mark.parametrize("selector", [".layout", ".page-layout"])
def test_two_column_views_use_a_296px_sidebar(selector):
    rule = _rule(selector)
    assert rule.get("display") == "grid"
    columns = rule.get("grid-template-columns", "")
    assert columns.endswith("296px"), columns


def test_narrow_views_switch_to_one_column():
    narrow = "(max-width: 1011px)"
    layout = _rule(".layout", narrow)
    assert layout.get("grid-template-columns") == "minmax(0, 1fr)"
    page = _rule(".page-layout", narrow)
    assert page.get("grid-template-columns") == "minmax(0, 1fr)"
    # Meta (About and Tags) above the body, Sources below it.
    assert page.get("grid-template-areas") == '"meta" "body" "src"'


def test_page_layout_places_body_meta_and_sources():
    assert _rule(".page-layout > article").get("grid-area") == "body"
    assert _rule(".page-layout > .meta").get("grid-area") == "meta"
    assert _rule(".page-layout > .src").get("grid-area") == "src"


@pytest.mark.parametrize("selector", [
    ".site-header", ".brand", ".tabs", ".crumbs", ".box", ".box-head",
    ".rows", "aside .side-section", ".tags", ".theme", ".btn", ".pager",
])
def test_frame_carries_the_mockup_classes(selector):
    assert _rule(selector), selector


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
