"""Round-trip-safe wiki.yaml 'tags:' block writer (REQ-11).

A tag-command write must edit only the 'tags:' block: every other key, its
ordering, and any comment must survive byte-for-byte. These tests pin that the
non-tags regions are unchanged while the tags block reflects the new values."""

import yaml

from agent_wiki.config import parse_tag_vocabulary
from agent_wiki.tag_yaml import update_tags_block


# A wiki.yaml with sibling blocks (topics, conversations, sources), inline
# comments, and irregular formatting that a naive yaml round-trip would mangle.
WIKI = """\
# agent-wiki vault config
topics:  # the four standard topics
  - projects
  - decisions
  - research
default_topic: research

tags:
  mode: warn
  vocabulary:
    stt:
      - asr  # speech recognition

conversations:
  retention: 90  # days
sources:
  - raw/notes.md  # original
"""


def test_updates_tags_block_preserving_everything_else(tmp_path):
    wiki = tmp_path / "wiki.yaml"
    wiki.write_text(WIKI)

    update_tags_block(
        wiki,
        vocabulary={"stt": ["asr", "speech-to-text"], "gpu": []},
        mode="strict",
    )
    text = wiki.read_text()

    # Everything before the tags block is byte-identical (header comment, the
    # topics block with its inline comment, default_topic, the blank line).
    assert text.startswith(WIKI[: WIKI.index("tags:")])

    # The sibling blocks after tags survive verbatim, comments and all, and the
    # blank line separating tags from conversations is preserved.
    assert "\n\nconversations:" in text
    assert "conversations:\n  retention: 90  # days\n" in text
    assert "sources:\n  - raw/notes.md  # original\n" in text

    # Top-level key ordering is unchanged: tags stays between topics and
    # conversations.
    assert text.index("topics:") < text.index("tags:") < text.index("conversations:")

    # The tags block itself reflects the new mode and vocabulary.
    vocab = parse_tag_vocabulary(yaml.safe_load(text))
    assert vocab.mode == "strict"
    assert vocab.vocabulary == {"stt": ["asr", "speech-to-text"], "gpu": []}


def test_omitted_mode_keeps_the_existing_mode(tmp_path):
    wiki = tmp_path / "wiki.yaml"
    wiki.write_text(WIKI)

    update_tags_block(wiki, vocabulary={"gpu": []})
    vocab = parse_tag_vocabulary(yaml.safe_load(wiki.read_text()))

    assert vocab.mode == "warn"
    assert vocab.vocabulary == {"gpu": []}


def test_creates_a_tags_block_when_absent(tmp_path):
    wiki = tmp_path / "wiki.yaml"
    wiki.write_text("topics:\n  - research\ndefault_topic: research\n")

    update_tags_block(wiki, vocabulary={"stt": ["asr"]}, mode="warn")
    text = wiki.read_text()

    # The pre-existing keys are untouched.
    assert text.startswith("topics:\n  - research\ndefault_topic: research\n")
    vocab = parse_tag_vocabulary(yaml.safe_load(text))
    assert vocab.mode == "warn"
    assert vocab.vocabulary == {"stt": ["asr"]}
