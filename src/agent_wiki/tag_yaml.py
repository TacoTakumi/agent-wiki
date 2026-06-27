"""Round-trip-safe writer for a wiki.yaml 'tags:' block (REQ-11).

A `tag add` / `tag suggest --write` must edit only the 'tags:' block and leave
the rest of wiki.yaml — every other key, its ordering, and any comment — byte for
byte unchanged. A plain `yaml.safe_load` + `yaml.dump` destroys comments and
reorders keys; even ruamel.yaml's whole-file round-trip re-renders sibling blocks
(list indentation, comment columns, blank lines). So we splice textually: copy
the bytes outside the block verbatim and re-render only the block itself with
ruamel.yaml.

This is the single write helper for the tag vocabulary, shared by `tag add` and
`tag suggest --write`."""

import re
from io import StringIO
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import SingleQuotedScalarString

# A top-level 'tags:' mapping key — at column 0, not nested under anything.
_TAGS_KEY = re.compile(r"^tags\s*:")


def _render_tags_block(mode, vocabulary) -> str:
    """Serialize {mode, vocabulary} as a 'tags:' block ending in one newline.

    Aliases are coerced to plain lists so ruamel renders block sequences (and an
    empty list as `[]`), which `parse_tag_vocabulary` reads back faithfully."""
    yaml_rt = YAML()
    yaml_rt.default_flow_style = False
    # Quote 'off' so the scalar survives a YAML 1.1 reader (PyYAML), which would
    # otherwise read a bare 'off' back as the boolean False. warn/strict are not
    # YAML booleans, so they stay unquoted.
    mode_scalar = SingleQuotedScalarString(mode) if mode == "off" else mode
    payload = {
        "tags": {
            "mode": mode_scalar,
            "vocabulary": {str(k): list(v) for k, v in dict(vocabulary).items()},
        }
    }
    buf = StringIO()
    yaml_rt.dump(payload, buf)
    text = buf.getvalue()
    if not text.endswith("\n"):
        text += "\n"
    return text


def _existing_mode(text: str) -> "str | None":
    """The current tags-block mode, or None when there is no readable block."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if isinstance(data, dict) and isinstance(data.get("tags"), dict):
        mode = data["tags"].get("mode")
        if mode is False:  # YAML 1.1 coerced a bare 'off' → boolean False
            return "off"
        return None if mode is None else str(mode)
    return None


def update_tags_block(path, vocabulary, *, mode=None) -> None:
    """Round-trip-safe rewrite of the 'tags:' block in the wiki.yaml at `path`.

    Replaces the block's vocabulary (a preferred→aliases mapping) and, when
    given, its mode; an omitted `mode` keeps the existing one, defaulting to
    'warn' when the block is being created. Only the block's own span is
    re-rendered, so every byte outside it — other keys, their order, and any
    comment — is preserved exactly. A missing block is appended at end of file."""
    path = Path(path)
    text = path.read_text()
    lines = text.splitlines(keepends=True)

    if mode is None:
        mode = _existing_mode(text) or "warn"
    block = _render_tags_block(mode, vocabulary)

    start = next((i for i, ln in enumerate(lines) if _TAGS_KEY.match(ln)), None)
    if start is None:
        # No tags block: append one, guaranteeing a newline at the seam.
        prefix = text if text.endswith("\n") or text == "" else text + "\n"
        path.write_text(prefix + block)
        return

    # The block runs until the next column-0 non-blank line (the next top-level
    # key or a column-0 comment), or EOF.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln[0].isspace():
            end = i
            break
    # Leave any trailing blank lines with the following content, not the block,
    # so a blank-line separator before the next block is preserved.
    while end - 1 > start and lines[end - 1].strip() == "":
        end -= 1

    before = "".join(lines[:start])
    after = "".join(lines[end:])
    path.write_text(before + block + after)
