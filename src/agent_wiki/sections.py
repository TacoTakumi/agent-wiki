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
