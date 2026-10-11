"""The theme switch: it sits on the user page, not in the header. POST
/theme, from a signed-in session that echoes its secret in the form's
session field, stores the choice in a cookie and redirects back on the same
site; every viewer page writes data-theme from that cookie and varies on
Cookie, and the user page marks the current button pressed. (The session
field itself is tested in test_viewer_csrf.py.)"""

import re
from pathlib import Path

import pytest

from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.session import SESSION_COOKIE
from conftest import make_vault, signed_in_client


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731, "tokens": []}
CSS = (Path(__file__).resolve().parent.parent / "src" / "agent_wiki"
       / "viewer" / "static" / "viewer.css")

PAGE = """---
title: Foo Page
topic: research
tags: [alpha]
created: '2026-01-02'
updated: '2026-03-04'
---

# Foo Page

Body text.
"""


@pytest.fixture
def viewer(tmp_path, tmp_vault):
    main = make_vault(tmp_path / "main")
    (main / "research" / "foo.md").write_text(PAGE)
    catalog = build_vault_catalog(
        {"main": VaultEntry(name="main", path=main)})
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    return signed_in_client(app)


VIEWS = [
    "/",
    "/vaults/main/",
    "/vaults/main/research/",
    "/vaults/main/-/tags/alpha",
    "/vaults/main/research/foo",
    "/search?q=body",
]


def post_theme(viewer, data):
    """POST /theme as the signed-in viewer, the form echoing its session."""
    data = {"session": viewer.cookies.get(SESSION_COOKIE), **data}
    return viewer.post("/theme", data=data, follow_redirects=False)


# ---------- POST /theme ----------

@pytest.mark.parametrize("value", ["auto", "light", "dark"])
def test_theme_post_sets_the_cookie_and_answers_303(viewer, value):
    resp = post_theme(viewer, {"theme": value, "next": "/vaults/main/"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/vaults/main/"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"awiki_theme={value};")
    lowered = cookie.lower()
    assert "path=/" in lowered
    assert "samesite=lax" in lowered
    assert "max-age=31536000" in lowered
    assert "httponly" in lowered


@pytest.mark.parametrize("value", ["purple", "", "AUTO", "dark "])
def test_theme_post_with_a_bad_value_answers_400_without_a_cookie(viewer,
                                                                  value):
    resp = post_theme(viewer, {"theme": value, "next": "/"})
    assert resp.status_code == 400
    assert "set-cookie" not in resp.headers


def test_theme_post_without_a_theme_answers_400(viewer):
    resp = post_theme(viewer, {"next": "/"})
    assert resp.status_code == 400
    assert "set-cookie" not in resp.headers


@pytest.mark.parametrize("next_url", [
    "//evil.example",
    "/\\evil.example",
    "https://evil.example",
    "javascript:alert(1)",
    "",
    "/a\tb",
    "/a\nb",
    "/a\x7fb",
])
def test_theme_redirect_refuses_other_sites(viewer, next_url):
    resp = post_theme(viewer, {"theme": "dark", "next": next_url})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_theme_redirect_without_next_goes_home(viewer):
    resp = post_theme(viewer, {"theme": "dark"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_theme_redirect_keeps_the_query(viewer):
    resp = post_theme(viewer, {"theme": "light",
                               "next": "/search?q=a&vault=main"})
    assert resp.headers["location"] == "/search?q=a&vault=main"


# ---------- data-theme on every page, the switch on the user page ----------

USER_PAGE = "/user"


def html_theme(html):
    match = re.search(r"<html\b[^>]*>", html)
    assert match, "no <html> tag"
    attr = re.search(r'\bdata-theme="([^"]*)"', match.group(0))
    assert attr, match.group(0)
    return attr.group(1)


def theme_form(html):
    form = re.search(r'<form class="theme" method="post" action="/theme"'
                     r'[^>]*>(.*?)</form>', html, re.S)
    assert form, "no theme form"
    return form.group(1)


def pressed(html):
    """The theme buttons as {value: aria-pressed}."""
    buttons = re.findall(r"<button\b[^>]*>[^<]*</button>", theme_form(html))
    out = {}
    for button in buttons:
        value = re.search(r'value="([^"]*)"', button).group(1)
        out[value] = re.search(r'aria-pressed="([^"]*)"', button).group(1)
    return out


def next_field(html):
    field = re.search(r'<input type="hidden" name="next" value="([^"]*)">',
                      theme_form(html))
    assert field, theme_form(html)
    return field.group(1)


@pytest.mark.parametrize("url", VIEWS + [USER_PAGE])
def test_no_cookie_means_auto(viewer, url):
    assert html_theme(viewer.get(url).text) == "auto"


@pytest.mark.parametrize("url", VIEWS + [USER_PAGE])
def test_dark_cookie_marks_dark(viewer, url):
    viewer.cookies.set("awiki_theme", "dark")
    assert html_theme(viewer.get(url).text) == "dark"


@pytest.mark.parametrize("url", VIEWS)
def test_no_page_but_the_user_page_has_the_switch(viewer, url):
    assert 'action="/theme"' not in viewer.get(url).text


def test_user_page_marks_auto_without_a_cookie(viewer):
    assert pressed(viewer.get(USER_PAGE).text) == {
        "auto": "true", "light": "false", "dark": "false"}


def test_user_page_marks_dark(viewer):
    viewer.cookies.set("awiki_theme", "dark")
    assert pressed(viewer.get(USER_PAGE).text) == {
        "auto": "false", "light": "false", "dark": "true"}


def test_light_cookie_marks_light(viewer):
    viewer.cookies.set("awiki_theme", "light")
    html = viewer.get(USER_PAGE).text
    assert html_theme(html) == "light"
    assert pressed(html)["light"] == "true"


def test_bogus_cookie_falls_back_to_auto(viewer):
    viewer.cookies.set("awiki_theme", "bogus")
    html = viewer.get(USER_PAGE).text
    assert html_theme(html) == "auto"
    assert pressed(html)["auto"] == "true"


def test_switch_shows_auto_light_and_dark_buttons(viewer):
    form = theme_form(viewer.get(USER_PAGE).text)
    for label in ("Auto", "Light", "Dark"):
        assert re.search(rf'<button[^>]*name="theme"[^>]*>{label}</button>',
                         form), label


def test_switch_comes_back_to_the_user_page(viewer):
    assert next_field(viewer.get(USER_PAGE).text) == USER_PAGE


def test_switch_on_the_user_page_sets_the_cookie(viewer):
    html = viewer.get(USER_PAGE).text
    secret = re.search(r'<input type="hidden" name="session" value="([^"]*)">',
                       theme_form(html)).group(1)
    resp = viewer.post("/theme", data={"session": secret, "theme": "dark",
                                       "next": next_field(html)},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == USER_PAGE
    assert resp.headers["set-cookie"].startswith("awiki_theme=dark;")
    assert pressed(viewer.get(USER_PAGE).text)["dark"] == "true"


# ---------- Stylesheet ----------

@pytest.mark.parametrize("selector, scheme", [
    ('[data-theme="auto"]', "light dark"),
    ('[data-theme="light"]', "light"),
    ('[data-theme="dark"]', "dark"),
])
def test_stylesheet_sets_color_scheme_per_theme(selector, scheme):
    css = CSS.read_text()
    rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rule, selector
    decl = re.search(r"color-scheme\s*:\s*([^;]+);", rule.group(1))
    assert decl, rule.group(1)
    assert " ".join(decl.group(1).split()) == scheme


# ---------- Caching ----------

@pytest.mark.parametrize("url", VIEWS + [USER_PAGE])
def test_every_view_varies_on_cookie(viewer, url):
    resp = viewer.get(url)
    assert resp.status_code == 200
    vary = [v.strip().lower() for v in resp.headers.get("vary", "").split(",")]
    assert "cookie" in vary
