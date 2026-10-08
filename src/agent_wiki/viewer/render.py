"""Turn a page's markdown body into sanitised HTML for the viewer.

Rendering is two pure steps: markdown-it-py parses the body (raw HTML in the
body is passed through), then nh3 cleans the result against an allowlist. The
sanitiser is the safety boundary: whatever the parser emits, script and style
elements, event-handler attributes and javascript: URLs never reach the page.
"""

import copy

import nh3
from markdown_it import MarkdownIt


# nh3's default allowlist already drops script/style (with their content),
# on* handlers and non-http(s)/mailto URL schemes. On top of it keep the
# fenced-code language class and the text-align style markdown-it puts on
# aligned table cells.
ALLOWED_ATTRIBUTES = copy.deepcopy(nh3.ALLOWED_ATTRIBUTES)
ALLOWED_ATTRIBUTES.setdefault("code", set()).add("class")
ALLOWED_ATTRIBUTES["th"].add("style")
ALLOWED_ATTRIBUTES["td"].add("style")
ALLOWED_STYLE_PROPERTIES = {"text-align"}


def build_parser() -> MarkdownIt:
    """CommonMark with GFM tables and strikethrough, raw HTML allowed (it is
    sanitised afterwards). New inline rules hook in here."""
    return MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])


def sanitise(html: str) -> str:
    """Clean rendered HTML against the viewer's allowlist."""
    return nh3.clean(
        html,
        attributes=ALLOWED_ATTRIBUTES,
        filter_style_properties=ALLOWED_STYLE_PROPERTIES,
    )


def render_markdown(body: str) -> str:
    """Render a markdown page body to sanitised HTML."""
    return sanitise(build_parser().render(body))
