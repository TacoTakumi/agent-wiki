"""Turn a page's markdown body into sanitised HTML for the viewer.

Rendering is two pure steps: markdown-it-py parses the body (raw HTML in the
body is passed through), then nh3 cleans the result against an allowlist. The
sanitiser is the safety boundary: whatever the parser emits, script and style
elements, event-handler attributes and javascript: URLs never reach the page.

A [[...]] in the body is a wikilink. It is an inline rule, so code spans and
fenced code keep it as literal text. The whole bracket text is looked up as an
exact page title (no Obsidian alias or callout handling); an unknown title
renders as <span class="missing">.

The page view shows the frontmatter title as its own H1. When render_markdown
gets that title and the body's first block is an H1 whose text, stripped of
surrounding whitespace, equals it, that H1 is left out so the title shows once.
Any other first H1, and any later H1, stays.
"""

import copy
import re

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
ALLOWED_ATTRIBUTES.setdefault("span", set()).add("class")
ALLOWED_STYLE_PROPERTIES = {"text-align"}

# The same bracket rule as page.extract_wikilinks, so the viewer links exactly
# what lint checks.
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _wikilink_rule(state, silent: bool) -> bool:
    """Inline rule: turn [[Title]] into a link to the page with that title, or
    a span with class missing when no page has it. Titles come from
    state.env["wikilinks"], a title -> URL map."""
    if not state.src.startswith("[[", state.pos):
        return False
    match = WIKILINK_RE.match(state.src, state.pos)
    if match is None or match.end() > state.posMax:
        return False
    if not silent:
        title = match.group(1)
        url = state.env.get("wikilinks", {}).get(title)
        if url is None:
            token = state.push("wikilink_missing_open", "span", 1)
            token.attrs = {"class": "missing"}
            state.push("text", "", 0).content = title
            state.push("wikilink_missing_close", "span", -1)
        else:
            token = state.push("link_open", "a", 1)
            token.attrs = {"href": url}
            state.push("text", "", 0).content = title
            state.push("link_close", "a", -1)
    state.pos = match.end()
    return True


def build_parser() -> MarkdownIt:
    """CommonMark with GFM tables and strikethrough, raw HTML allowed (it is
    sanitised afterwards). The wikilink rule runs before the link rule so
    [[...]] is never read as a markdown link."""
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.inline.ruler.before("link", "wikilink", _wikilink_rule)
    return md


def sanitise(html: str) -> str:
    """Clean rendered HTML against the viewer's allowlist."""
    return nh3.clean(
        html,
        attributes=ALLOWED_ATTRIBUTES,
        filter_style_properties=ALLOWED_STYLE_PROPERTIES,
    )


def drop_title_heading(tokens: list, title: str) -> list:
    """Leave out the first block when it is an H1 whose raw inline text,
    stripped, equals title (also stripped). Other tokens are kept as is."""
    if (len(tokens) >= 3
            and tokens[0].type == "heading_open" and tokens[0].tag == "h1"
            and tokens[1].type == "inline"
            and tokens[1].content.strip() == title.strip()
            and tokens[2].type == "heading_close"):
        return tokens[3:]
    return tokens


def render_markdown(body: str, links: dict[str, str] | None = None,
                    title: str | None = None) -> str:
    """Render a markdown page body to sanitised HTML. links maps an exact page
    title to its URL; a wikilink to a title not in it renders as missing. With
    title, a first-block H1 that repeats it is left out."""
    md = build_parser()
    env = {"wikilinks": links or {}}
    tokens = md.parse(body, env)
    if title is not None:
        tokens = drop_title_heading(tokens, title)
    return sanitise(md.renderer.render(tokens, md.options, env))
