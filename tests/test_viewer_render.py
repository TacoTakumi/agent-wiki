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
