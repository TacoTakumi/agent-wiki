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
