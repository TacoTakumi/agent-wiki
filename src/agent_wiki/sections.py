"""Pure, I/O-free markdown slicing primitives: the heading scanner and the
frontmatter stripper.

Every sliced view of a page (outline, one section, first/last N children) is
built on `scan_headings`, so what counts as a heading is defined in exactly one
place: an ATX line of 1-6 '#' followed by a space, outside any fenced code
block. Setext headings are deliberately not recognised.
"""

import re
from typing import NamedTuple

import yaml

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
    lines = text.split("\n")

    for index, (raw, in_fence) in enumerate(zip(lines, _fence_map(lines))):
        if in_fence:
            continue
        heading = _HEADING_RE.match(raw)
        if heading:
            headings.append(
                (index, len(heading.group(1)), heading.group(2).strip(), raw)
            )

    return headings


def _fence_map(lines: list[str]) -> list[bool]:
    """For each line, whether it lies inside a fenced code block.

    The fence delimiter lines themselves count as inside. A fence opens on
    three or more backticks or tildes and closes only on a fence of the same
    character, at least as long, with nothing after it but whitespace; an
    unclosed fence runs to the end.
    """
    inside: list[bool] = []
    fence: tuple[str, int] | None = None

    for raw in lines:
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
            inside.append(True)
            continue
        inside.append(fence is not None)

    return inside


def strip_frontmatter(text: str) -> str:
    """Return `text` without a leading YAML frontmatter block.

    The block runs from an opening '---' on the first line to the next line
    that is '---' on its own, and must parse as a YAML mapping - so a page that
    opens with a '---' thematic break, or a stray divider, keeps its text. The
    single blank line separating a real block from the body goes with it. Text
    without a well-formed leading block - including one that is never closed -
    is returned unchanged.
    """
    if not text.startswith("---\n"):
        return text

    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        try:
            meta = yaml.safe_load("\n".join(lines[1:index]))
        except yaml.YAMLError:
            return text
        if not isinstance(meta, dict):
            return text
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


class Section(NamedTuple):
    """A selected section: its text, and the heading lines that also matched
    the query but fall outside it (the ones the caller did not get)."""

    text: str
    others: list[str]


def _match(headings: list[tuple[int, int, str, str]],
           query: str) -> list[tuple[int, int, str, str]]:
    """The one match rule: case-insensitive substring against the heading text
    (the line minus its leading '#' marks), both sides whitespace-trimmed."""
    needle = query.strip().lower()
    return [h for h in headings if needle in h[2].lower()]


def select_section(text: str, query: str) -> "Section | None":
    """Return the first section of `text` whose heading matches `query`.

    The section runs from its heading line through the line before the next
    heading of the same or a higher level, or to the end of the text - so
    deeper subsections come with it. Returns None when no heading matches.

    `others` carries the heading lines that matched but lie outside the
    returned section; a match nested inside it is already in the text, so it
    is not reported as missed.

    Trailing blank lines are dropped and the result ends in exactly one
    newline, so a section that ends at a heading and one that ends at EOF read
    the same.
    """
    headings = scan_headings(text)
    matches = _match(headings, query)
    if not matches:
        return None

    start, level = matches[0][0], matches[0][1]
    lines = text.split("\n")
    end = len(lines)
    for later_index, later_level, _, _ in headings:
        if later_index > start and later_level <= level:
            end = later_index
            break

    others = [h[3] for h in matches[1:] if not start <= h[0] < end]
    return Section(_as_block(lines[start:end]), others)


def _as_block(lines: list[str]) -> str:
    """Join `lines` into text ending in exactly one newline, blank tail dropped.

    A trailing empty element is the artifact of the source text's final
    newline, not a blank line, so it goes first. Blank lines inside an
    unterminated fenced code block are content and are kept - so text ending
    in an open fence is the only result that can come back with a blank tail,
    and the only non-empty result that can fail to end in exactly one newline
    (a kept tail of whitespace-only lines still ends in one). An empty result
    is the empty string, never a bare newline.
    """
    if lines and lines[-1] == "":
        lines = lines[:-1]
    inside = _fence_map(lines)
    end = len(lines)
    while end and not lines[end - 1].strip() and not inside[end - 1]:
        end -= 1
    return "".join(f"{line}\n" for line in lines[:end])


def slice_children(text: str, head: int | None = None,
                   tail: int | None = None) -> str:
    """Return `text` reduced to its first `head` or last `tail` child sections.

    `text` is a section: its first heading is the section's own heading, and a
    direct child is a heading below it that is not nested under another heading
    inside the section. The section heading and its preamble (everything before
    the first child) are always kept, and each kept child brings its own
    subsections. A count beyond the number of children keeps them all, and a
    count of zero keeps the preamble alone. A section with no children is still
    re-emitted through `_as_block` - trailing blank lines dropped, one final
    newline - so it comes back byte-identical only if it already ends that way.
    Called with neither count, `text` is returned verbatim.
    """
    if head is None and tail is None:
        return text

    lines = text.split("\n")
    children = _direct_children(scan_headings(text)[1:])
    if not children:
        return _as_block(lines)

    return _keep(lines, children, head, tail)


def slice_top_level(text: str, head: int | None = None,
                    tail: int | None = None) -> str:
    """Return `text` reduced to its first `head` or last `tail` top-level sections.

    The top-level sections are the H1's direct children when the first heading
    is the page's only H1; otherwise they are the sections at the shallowest
    heading level present. Everything before the first of them - the H1 line
    and any preamble - is kept, and a count of zero keeps that alone. A page
    with no headings is still re-emitted through `_as_block` - trailing blank
    lines dropped, one final newline - so it comes back byte-identical only if
    it already ends that way. Called with neither count, `text` is returned
    verbatim.
    """
    if head is None and tail is None:
        return text

    headings = scan_headings(text)
    if not headings:
        return _as_block(text.split("\n"))

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
    # Count from the front for the tail too: bounds[-tail:] would hand back the
    # whole list on a count of zero, where head=0 correctly keeps none. A
    # negative count is undefined either way; the CLI rejects anything below 1.
    kept = (bounds[:head] if head is not None
            else bounds[max(len(bounds) - tail, 0):])

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
