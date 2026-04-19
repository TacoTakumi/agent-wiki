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
