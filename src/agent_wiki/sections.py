"""Pure, I/O-free markdown slicing primitives: the heading scanner and the
frontmatter stripper.

Every sliced view of a page (outline, one section, first/last N children) is
built on `scan_headings`, so what counts as a heading is defined in exactly one
place: an ATX line of 1-6 '#' followed by a space, outside any fenced code
block. Setext headings are deliberately not recognised.
"""

import re

# Up to three leading spaces, 1-6 '#', then at least one space before the text.
# Four or more leading spaces is an indented code block, and '#hash' with no
# space is ordinary text.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6}) +(.*)$")

# A fence opens on three or more backticks or tildes and closes on a fence of
# the same character at least as long, with nothing after it but whitespace.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def scan_headings(text: str) -> list[tuple[int, int, str, str]]:
    """Return every ATX heading in `text` as (line_index, level, text, raw_line).

    `line_index` is the 0-based index into `text.split("\\n")`, `level` the
    number of '#', `text` the heading text with surrounding whitespace trimmed,
    and `raw_line` the source line verbatim (so callers can echo it unchanged).
    Lines inside a fenced code block are skipped; an unclosed fence runs to the
    end of the text.
    """
    headings: list[tuple[int, int, str, str]] = []
    fence: tuple[str, int] | None = None

    for index, raw in enumerate(text.split("\n")):
        fence_match = _FENCE_RE.match(raw)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif (
                marker[0] == fence[0]
                and len(marker) >= fence[1]
                and raw.strip() == marker
            ):
                fence = None
            continue
        if fence is not None:
            continue
        heading = _HEADING_RE.match(raw)
        if heading:
            headings.append(
                (index, len(heading.group(1)), heading.group(2).strip(), raw)
            )

    return headings


def strip_frontmatter(text: str) -> str:
    """Return `text` without a leading YAML frontmatter block.

    The block runs from an opening '---' on the first line to the next line
    that is '---' on its own; the single blank line separating it from the body
    goes with it. Text without a well-formed leading block - including one that
    is never closed - is returned unchanged.
    """
    if not text.startswith("---\n"):
        return text

    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            rest = lines[index + 1:]
            if rest and rest[0] == "":
                rest = rest[1:]
            return "\n".join(rest)

    return text


def render_outline(text: str) -> str:
    """Return the heading lines of `text` verbatim, one per line.

    The '#' marks and text are exactly as they appear in the source, in file
    order, with nothing else - no body text, no numbering.
    """
    return "".join(f"{raw}\n" for _, _, _, raw in scan_headings(text))


def match_headings(text: str, query: str) -> list[tuple[int, int, str, str]]:
    """Return the headings of `text` whose text matches `query`, in file order.

    A match is a case-insensitive substring test against the heading text (the
    line minus its leading '#' marks, whitespace-trimmed); `query` is trimmed
    the same way.
    """
    needle = query.strip().lower()
    return [h for h in scan_headings(text) if needle in h[2].lower()]


def select_section(text: str, query: str) -> str | None:
    """Return the first section of `text` whose heading matches `query`.

    The section runs from its heading line through the line before the next
    heading of the same or a higher level, or to the end of the text - so
    deeper subsections come with it. Returns None when no heading matches.

    Trailing blank lines are dropped and the result ends in exactly one
    newline, so a section that ends at a heading and one that ends at EOF read
    the same.
    """
    headings = scan_headings(text)
    lines = text.split("\n")
    needle = query.strip().lower()

    for position, (index, level, heading_text, _raw) in enumerate(headings):
        if needle not in heading_text.lower():
            continue
        end = len(lines)
        for later_index, later_level, _, _ in headings[position + 1:]:
            if later_level <= level:
                end = later_index
                break
        return _as_block(lines[index:end])

    return None


def _as_block(lines: list[str]) -> str:
    """Join `lines` into text ending in exactly one newline, blank tail dropped."""
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "".join(f"{line}\n" for line in lines)


def slice_children(text: str, head: int | None = None,
                   tail: int | None = None) -> str:
    """Return `text` reduced to its first `head` or last `tail` child sections.

    `text` is a section: its first heading is the section's own heading, and a
    direct child is a heading below it that is not nested under another heading
    inside the section. The section heading and its preamble (everything before
    the first child) are always kept, and each kept child brings its own
    subsections. A count beyond the number of children keeps them all, and a
    section with no children is returned unchanged.
    """
    if head is None and tail is None:
        return text

    headings = scan_headings(text)
    children = _direct_children(headings[1:])
    if not children:
        return text

    return _keep(text.split("\n"), children, head, tail)


def slice_top_level(text: str, head: int | None = None,
                    tail: int | None = None) -> str:
    """Return `text` reduced to its first `head` or last `tail` top-level sections.

    The top-level sections are the H1's direct children when the first heading
    is the page's only H1; otherwise they are the sections at the shallowest
    heading level present. Everything before the first of them - the H1 line
    and any preamble - is kept, and a page with no headings is returned
    unchanged.
    """
    if head is None and tail is None:
        return text

    headings = scan_headings(text)
    if not headings:
        return text

    if headings[0][1] == 1 and sum(1 for h in headings if h[1] == 1) == 1:
        return slice_children(text, head=head, tail=tail)

    shallowest = min(h[1] for h in headings)
    tops = [h for h in headings if h[1] == shallowest]
    return _keep(text.split("\n"), tops, head, tail)


def _keep(lines: list[str], sections: list[tuple[int, int, str, str]],
          head: int | None, tail: int | None) -> str:
    """Keep the lines before `sections` plus the first/last N of them.

    Each section runs to the start of the next one, so its own subsections
    come with it; the last runs to the end of `lines`.
    """
    starts = [section[0] for section in sections]
    bounds = list(zip(starts, starts[1:] + [len(lines)]))
    kept = bounds[:head] if head is not None else bounds[-tail:]

    sliced = lines[:starts[0]]
    for start, end in kept:
        sliced += lines[start:end]
    return _as_block(sliced)


def _direct_children(
    inner: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    """Of the headings inside a section, those not nested under another one.

    A heading is a direct child when no earlier heading in the section is
    shallower than it, so a run of deeper headings attaches to the child above
    them rather than becoming children in its own right.
    """
    children = []
    shallowest = None
    for heading in inner:
        if shallowest is None or heading[1] <= shallowest:
            children.append(heading)
        shallowest = heading[1] if shallowest is None else min(shallowest, heading[1])
    return children
