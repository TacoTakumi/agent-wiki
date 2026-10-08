import re

from agent_wiki.viewer.render import render_markdown


HOSTILE_BODY = """\
# Heading

<script>alert(1)</script>

<img src=x onerror=alert(1)>

[x](javascript:alert(1))

<a href="javascript:alert(2)">y</a>

<style>body { display: none }</style>

| Name | Value |
| ---- | ----- |
| a    | 1     |

```python
print("hi")
```

[ok](https://example.com) ![pic](https://example.com/p.png)
"""


def test_render_drops_script_handlers_and_javascript_urls():
    html = render_markdown(HOSTILE_BODY)
    assert "<script" not in html.lower()
    assert "<style" not in html.lower()
    assert "onerror" not in html.lower()
    assert not re.search(r"href\s*=\s*[\"']?\s*javascript:", html, re.IGNORECASE)


def test_render_keeps_tables_and_fenced_code():
    html = render_markdown(HOSTILE_BODY)
    assert "<table>" in html
    assert "<th>Name</th>" in html
    assert "<td>a</td>" in html
    assert "<pre><code" in html
    assert "print(&quot;hi&quot;)" in html or 'print("hi")' in html


def test_render_keeps_headings_links_and_images():
    html = render_markdown(HOSTILE_BODY)
    assert "<h1>Heading</h1>" in html
    assert 'href="https://example.com"' in html
    assert 'src="https://example.com/p.png"' in html
    assert 'alt="pic"' in html


def test_render_keeps_fenced_code_language_class():
    html = render_markdown("```python\nx = 1\n```\n")
    assert 'class="language-python"' in html


LINKS = {"Alpha Page": "/vaults/main/research/alpha-page", "A": "/vaults/main/research/a"}


def test_wikilink_to_known_title_becomes_anchor():
    html = render_markdown("See [[Alpha Page]] here.\n", links=LINKS)
    assert '<a href="/vaults/main/research/alpha-page"' in html
    assert ">Alpha Page</a>" in html
    assert "[[" not in html


def test_wikilink_to_unknown_title_is_missing_span_without_href():
    html = render_markdown("See [[Nowhere]] here.\n", links=LINKS)
    assert '<span class="missing">Nowhere</span>' in html
    assert "href" not in html


def test_wikilink_bracket_text_is_escaped():
    html = render_markdown("[[<b>x</b> & y]]\n", links={})
    assert "<b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in html


def test_wikilink_in_inline_and_fenced_code_stays_literal():
    body = "Use `[[Alpha Page]]` inline.\n\n```\n[[Alpha Page]]\n```\n"
    html = render_markdown(body, links=LINKS)
    assert "<code>[[Alpha Page]]</code>" in html
    assert "<pre><code>[[Alpha Page]]\n</code></pre>" in html
    assert "href" not in html


def test_wikilink_alias_syntax_is_looked_up_as_full_text():
    html = render_markdown("[[A|b]]\n", links=LINKS)
    assert '<span class="missing">A|b</span>' in html
    assert "href" not in html


def test_callout_renders_as_plain_blockquote():
    html = render_markdown("> [!note]\n> Body text.\n", links=LINKS)
    assert "<blockquote>" in html
    assert "[!note]" in html
    assert "Body text." in html


# The page view shows the title as its own H1, so a body that opens with the
# same H1 would show it twice.

TITLE_BODY = "# Foo Page\n\nText.\n"


def test_title_first_h1_matching_title_is_dropped():
    html = render_markdown(TITLE_BODY, title="Foo Page")
    assert "<h1" not in html
    assert "<p>Text.</p>" in html


def test_title_match_ignores_surrounding_whitespace():
    html = render_markdown("\n\n#   Foo Page   \n\nText.\n", title="  Foo Page ")
    assert "<h1" not in html


def test_title_first_h1_with_other_text_is_kept():
    html = render_markdown("# Other\n\nText.\n", title="Foo Page")
    assert "<h1>Other</h1>" in html


def test_title_later_matching_h1_is_kept():
    html = render_markdown("Intro.\n\n# Foo Page\n", title="Foo Page")
    assert "<h1>Foo Page</h1>" in html


def test_title_first_h2_matching_title_is_kept():
    html = render_markdown("## Foo Page\n", title="Foo Page")
    assert "<h2>Foo Page</h2>" in html


def test_title_absent_keeps_first_h1():
    assert "<h1>Foo Page</h1>" in render_markdown(TITLE_BODY)


def test_title_page_view_shows_one_h1(tmp_vault, tmp_path):
    from fastapi.testclient import TestClient

    from agent_wiki.registry import VaultEntry
    from agent_wiki.server.app import create_app
    from agent_wiki.viewer.catalog import build_vault_catalog
    from conftest import make_vault
    from test_viewer_app import SERVER_CONFIG

    main = make_vault(tmp_path / "main")
    (main / "research" / "foo.md").write_text(
        "---\ntitle: Foo Page\ntopic: research\n---\n\n"
        "# Foo Page\n\nBody text.\n")
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=main)})
    client = TestClient(create_app(tmp_vault, SERVER_CONFIG, viewer=catalog))
    html = client.get("/vaults/main/research/foo").text
    assert "Body text." in html
    assert len(re.findall(r"<h1[\s>]", html)) == 1
