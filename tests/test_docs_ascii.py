"""The published docs and the packaged memory block stay pure ASCII.

README.md and CHANGELOG.md are what people read on GitHub and PyPI, and
``data/guide.md`` is pasted verbatim into an agent's memory file. Keeping all
three ASCII makes them render identically everywhere (terminals, plain editors,
models with narrow tokenizers) with no encoding surprises, and stops a stray em
dash / smart quote / arrow from creeping back in on the next edit.
"""

from pathlib import Path

import pytest

from agent_wiki.guide import render_block

REPO_ROOT = Path(__file__).resolve().parent.parent

# Docs published to the public repo and to PyPI.
ASCII_DOCS = ["README.md", "CHANGELOG.md"]


def _report(name: str, text: str) -> str:
    offenders = sorted({ch for ch in text if ord(ch) > 0x7F})
    return f"{name} contains non-ASCII characters: " + ", ".join(
        f"{ch!r} (U+{ord(ch):04X})" for ch in offenders
    )


@pytest.mark.parametrize("name", ASCII_DOCS)
def test_published_doc_is_pure_ascii(name):
    path = REPO_ROOT / name
    text = path.read_text(encoding="utf-8")
    assert text.isascii(), _report(name, text)


def test_packaged_guide_block_is_pure_ascii():
    # The block is byte-identical to a fenced section of README.md
    # (see tests/test_guide.py), so it has to clear the same bar.
    block = render_block()
    assert block.isascii(), _report("data/guide.md", block)
