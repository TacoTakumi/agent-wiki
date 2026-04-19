import pytest

from agent_wiki.context import should_skip


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
