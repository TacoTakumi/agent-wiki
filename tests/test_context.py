import pytest

from agent_wiki.context import should_skip, extract_keywords


@pytest.mark.parametrize("prompt,expected", [
    ("", True),
    ("   ", True),
    ("ok", True),
    ("/gsd-next", True),
    ("/help me please do a thing", True),
    ("run the tests", True),  # 3 words but only 13 chars
    ("how do I run tests", False),  # 5 words, 18 chars
    ("what is the ingest pipeline", False),
    ("  what is the ingest pipeline  ", False),  # leading/trailing ws
])
def test_should_skip(prompt, expected):
    assert should_skip(prompt) is expected


def test_extract_keywords_returns_empty_for_empty_input():
    assert extract_keywords("") == []
    assert extract_keywords("   ") == []


def test_extract_keywords_returns_empty_for_very_short_input():
    # Below YAKE's useful range — don't even invoke it.
    assert extract_keywords("ok") == []


def test_extract_keywords_returns_nonempty_for_real_prose():
    kws = extract_keywords("how do I configure the ingest pipeline for codex sessions")
    assert len(kws) > 0
    assert all(isinstance(k, str) for k in kws)


def test_extract_keywords_respects_max_keywords():
    text = "the ingest pipeline codex sessions configuration tooling adapter"
    kws = extract_keywords(text, max_keywords=3)
    assert len(kws) <= 3


def test_extract_keywords_never_raises_on_weird_input():
    # Smoke: YAKE should handle punctuation, unicode, etc. If it doesn't,
    # our wrapper should still return [] rather than raising.
    assert isinstance(extract_keywords("!!!???"), list)
    assert isinstance(extract_keywords("😀 emoji prompt here"), list)


from agent_wiki.context import build_context_block


def test_build_context_block_empty_hits_returns_empty_string():
    assert build_context_block([], topic_order=["research"]) == ""


def test_build_context_block_single_hit_single_topic():
    hits = [{"title": "Ingest Pipeline", "path": "research/ingest-pipeline.md", "matches": ["x"]}]
    block = build_context_block(hits, topic_order=["research", "tools"])
    assert "<!-- agent-wiki: 1 possibly-relevant page" in block
    assert "## research" in block
    assert "- [Ingest Pipeline](research/ingest-pipeline.md)" in block
    assert "## tools" not in block


def test_build_context_block_groups_and_orders_by_topic():
    hits = [
        {"title": "Tool A", "path": "tools/a.md", "matches": ["x"]},
        {"title": "Research A", "path": "research/a.md", "matches": ["x"]},
        {"title": "Tool B", "path": "tools/b.md", "matches": ["x"]},
    ]
    block = build_context_block(hits, topic_order=["research", "tools"])
    # research must appear before tools even though tools came first in the hits list.
    assert block.index("## research") < block.index("## tools")


def test_build_context_block_enforces_cap():
    hits = [
        {"title": f"Page {i}", "path": f"research/{i}.md", "matches": ["x"]}
        for i in range(10)
    ]
    block = build_context_block(hits, topic_order=["research"], limit=3)
    assert "<!-- agent-wiki: 3 possibly-relevant pages" in block
    # Only 3 bullets rendered.
    assert block.count("- [Page") == 3


def test_build_context_block_hides_unknown_topics():
    # Paths at vault root (not in any topic) should be silently dropped.
    hits = [
        {"title": "Research A", "path": "research/a.md", "matches": ["x"]},
        {"title": "Orphan", "path": "orphan.md", "matches": ["x"]},
    ]
    block = build_context_block(hits, topic_order=["research"])
    assert "Research A" in block
    assert "Orphan" not in block


def test_build_context_block_mentions_awiki_search_skill():
    hits = [{"title": "X", "path": "research/x.md", "matches": ["m"]}]
    block = build_context_block(hits, topic_order=["research"])
    assert "awiki-search" in block


import yaml as _yaml

from agent_wiki.context import run_context


def _seed_page(vault, topic, slug, title, body):
    page = vault / topic / f"{slug}.md"
    page.write_text(
        f"---\ntitle: {title}\ntopic: {topic}\n---\n\n# {title}\n\n{body}\n"
    )


def test_run_context_returns_block_when_hits_found(tmp_vault):
    _seed_page(tmp_vault, "research", "ingest-pipeline",
               "Ingest Pipeline", "The ingest pipeline handles codex sessions.")
    result = run_context(
        "how do I configure the ingest pipeline for codex sessions",
        tmp_vault,
    )
    assert result is not None
    assert "Ingest Pipeline" in result


def test_run_context_returns_none_when_no_hits(tmp_vault):
    result = run_context("tell me about quantum tunneling in semiconductors", tmp_vault)
    assert result is None


def test_run_context_returns_none_when_prompt_should_skip(tmp_vault):
    _seed_page(tmp_vault, "research", "x", "X", "ingest pipeline")
    assert run_context("ok", tmp_vault) is None
    assert run_context("/gsd-next", tmp_vault) is None


def test_run_context_returns_none_when_toggle_off(tmp_vault, monkeypatch):
    _seed_page(tmp_vault, "research", "x", "X", "ingest pipeline")
    monkeypatch.setenv("AWIKI_AUTO_CONTEXT", "0")
    assert run_context(
        "how do I configure the ingest pipeline",
        tmp_vault,
    ) is None


def test_run_context_returns_none_when_no_wiki_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("AWIKI_AUTO_CONTEXT", raising=False)
    assert run_context(
        "how do I configure the ingest pipeline",
        tmp_path,
    ) is None
