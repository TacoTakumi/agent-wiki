from unittest.mock import patch, MagicMock

import pytest

from agent_wiki.conversation import Conversation
from agent_wiki.summarize import (
    ClaudePSummarizer,
    LocalOpenAISummarizer,
    NoneSummarizer,
    make_summarizer,
)


def _conv() -> Conversation:
    return Conversation(
        agent="claude-code",
        session_id="s1",
        title="Test",
        body="## user\nhi\n## assistant\nhello\n",
    )


def test_none_summarizer_returns_none():
    assert NoneSummarizer().summarize(_conv()) is None


def test_make_summarizer_default_is_none():
    s = make_summarizer({})
    assert isinstance(s, NoneSummarizer)


def test_make_summarizer_selects_claude_p():
    s = make_summarizer({"type": "claude-p", "claude_p": {"args": ["-p", "-x"]}})
    assert isinstance(s, ClaudePSummarizer)
    assert s.args == ["-p", "-x"]


def test_make_summarizer_selects_local_openai():
    s = make_summarizer({
        "type": "local-openai",
        "local_openai": {
            "base_url": "http://localhost:1234/v1",
            "model": "llama",
            "max_tokens": 200,
        },
    })
    assert isinstance(s, LocalOpenAISummarizer)
    assert s.base_url == "http://localhost:1234/v1"
    assert s.model == "llama"
    assert s.max_tokens == 200


def test_claude_p_summarizer_shells_out_and_captures_stdout():
    s = ClaudePSummarizer(args=["-p"])
    fake = MagicMock(returncode=0, stdout="## Context\nall fine\n", stderr="")
    with patch("agent_wiki.summarize.subprocess.run", return_value=fake) as run:
        out = s.summarize(_conv())
    assert out == "## Context\nall fine"
    args, _ = run.call_args
    assert args[0][0] == "claude"
    assert "-p" in args[0]


def test_claude_p_summarizer_missing_binary_returns_none():
    s = ClaudePSummarizer()
    with patch("agent_wiki.summarize.subprocess.run", side_effect=FileNotFoundError):
        assert s.summarize(_conv()) is None


def test_claude_p_summarizer_nonzero_returncode_returns_none():
    s = ClaudePSummarizer()
    fake = MagicMock(returncode=2, stdout="", stderr="oops")
    with patch("agent_wiki.summarize.subprocess.run", return_value=fake):
        assert s.summarize(_conv()) is None


def test_local_openai_summarizer_posts_and_parses_response():
    s = LocalOpenAISummarizer(base_url="http://127.0.0.1:8080/v1", model="local")

    fake_resp = MagicMock()
    fake_resp.read.return_value = (
        b'{"choices":[{"message":{"content":"## Context\\nlocal works"}}]}'
    )
    # urlopen is used as a context manager
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("agent_wiki.summarize.urllib.request.urlopen", return_value=fake_resp) as urlopen:
        out = s.summarize(_conv())
    assert out == "## Context\nlocal works"
    req = urlopen.call_args[0][0]
    assert req.get_full_url() == "http://127.0.0.1:8080/v1/chat/completions"


def test_local_openai_summarizer_network_failure_returns_none():
    import urllib.error

    s = LocalOpenAISummarizer()
    with patch("agent_wiki.summarize.urllib.request.urlopen",
               side_effect=urllib.error.URLError("nope")):
        assert s.summarize(_conv()) is None


def test_local_openai_summarizer_malformed_response_returns_none():
    s = LocalOpenAISummarizer()
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"not json"
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)
    with patch("agent_wiki.summarize.urllib.request.urlopen", return_value=fake_resp):
        assert s.summarize(_conv()) is None
