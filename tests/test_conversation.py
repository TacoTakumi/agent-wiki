from datetime import datetime
from pathlib import Path

import pytest
import yaml

from agent_wiki.conversation import (
    BUNDLE_SUBDIR,
    Conversation,
    ingest_conversation,
    read_bundle,
    write_bundle,
)
from agent_wiki.page import parse_page


def _sample_conv(**overrides) -> Conversation:
    base = dict(
        agent="claude-code",
        session_id="abc-123",
        title="Sample Session",
        body="## user\nhello\n\n## assistant\nhi\n",
        project="agent-wiki",
        started=datetime(2026, 4, 18, 10, 0, 0),
        ended=datetime(2026, 4, 18, 10, 30, 0),
        model="claude-opus-4-7",
        turns=2,
        tool_counts={"bash": 1},
        token_totals={"input": 100, "output": 50},
    )
    base.update(overrides)
    return Conversation(**base)


def test_bundle_roundtrip(tmp_vault):
    conv = _sample_conv()
    path = write_bundle(conv, tmp_vault)

    assert path.exists()
    assert path.parent == tmp_vault / BUNDLE_SUBDIR
    assert path.name == "claude-code-abc-123.md"

    loaded = read_bundle(path)
    assert loaded.agent == conv.agent
    assert loaded.session_id == conv.session_id
    assert loaded.title == conv.title
    assert loaded.project == conv.project
    assert loaded.started == conv.started
    assert loaded.ended == conv.ended
    assert loaded.model == conv.model
    assert loaded.turns == conv.turns
    assert loaded.tool_counts == conv.tool_counts
    assert loaded.token_totals == conv.token_totals
    assert "hello" in loaded.body


def test_bundle_preserves_extra_keys(tmp_vault):
    conv = _sample_conv()
    conv.extra = {"branch": "feature/foo", "pr_url": "https://example/pr/1"}
    path = write_bundle(conv, tmp_vault)
    loaded = read_bundle(path)
    assert loaded.extra.get("branch") == "feature/foo"
    assert loaded.extra.get("pr_url") == "https://example/pr/1"


def test_bundle_id_slugifies_unsafe_ids():
    conv = _sample_conv(session_id="Weird ID/with\\chars")
    assert conv.bundle_id() == "claude-code-weird-idwithchars"


def test_read_bundle_rejects_wrong_type(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("---\ntype: document\ntitle: X\n---\n\nbody\n")
    with pytest.raises(ValueError, match="not a conversation bundle"):
        read_bundle(bad)


def test_read_bundle_rejects_missing_required_field(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("---\ntype: conversation\nagent: foo\nsession_id: s1\n---\n\nbody\n")
    with pytest.raises(ValueError, match="missing required frontmatter"):
        read_bundle(bad)


def test_ingest_conversation_creates_page(tmp_vault):
    conv = _sample_conv()
    bundle_path = write_bundle(conv, tmp_vault)

    page_path = ingest_conversation(bundle_path, tmp_vault)
    assert page_path.exists()
    assert page_path.parent.name == "sessions"

    page = parse_page(page_path)
    meta = page["meta"]
    assert meta["type"] == "conversation"
    assert meta["agent"] == "claude-code"
    assert meta["session_id"] == "abc-123"
    assert meta["title"] == "Sample Session"
    assert meta["project"] == "agent-wiki"
    assert meta["model"] == "claude-opus-4-7"
    assert meta["sources"] == [f"{BUNDLE_SUBDIR}/claude-code-abc-123.md"]
    assert "claude-code" in meta["tags"]
    assert f"[[{BUNDLE_SUBDIR}/claude-code-abc-123.md]]" in page["body"]


def test_ingest_conversation_respects_configured_topic(tmp_vault):
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    config.setdefault("conversations", {})["topic"] = "chats"
    config["topics"].append("chats")
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))

    conv = _sample_conv()
    bundle_path = write_bundle(conv, tmp_vault)

    page_path = ingest_conversation(bundle_path, tmp_vault)
    assert page_path.parent.name == "chats"


def test_ingest_conversation_appends_log(tmp_vault):
    conv = _sample_conv()
    bundle_path = write_bundle(conv, tmp_vault)

    ingest_conversation(bundle_path, tmp_vault)

    log = (tmp_vault / "log.md").read_text()
    assert "sync" in log
    assert "claude-code:abc-123" in log


def test_ingest_conversation_uses_summarizer(tmp_vault):
    conv = _sample_conv()
    bundle_path = write_bundle(conv, tmp_vault)

    class FakeSummarizer:
        def summarize(self, conversation):
            return "## Context\nAll fine.\n\n## Decisions\nNone."

    page_path = ingest_conversation(bundle_path, tmp_vault, summarizer=FakeSummarizer())
    body = parse_page(page_path)["body"]
    assert "## Context" in body
    assert "## Decisions" in body


def test_ingest_conversation_applies_redactor(tmp_vault):
    conv = _sample_conv(body="Email: alice@example.com\n", title="Leak alice@example.com")
    bundle_path = write_bundle(conv, tmp_vault)

    class FakeRedactor:
        def redact(self, text: str) -> str:
            return text.replace("alice@example.com", "[REDACTED]")

    page_path = ingest_conversation(bundle_path, tmp_vault, redactor=FakeRedactor())
    page = parse_page(page_path)
    # Page body does NOT contain raw transcript; it only links back. Title is redacted.
    assert "alice@example.com" not in page["meta"]["title"]
    assert "[REDACTED]" in page["meta"]["title"]


def test_ingest_conversation_missing_bundle(tmp_vault, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_conversation(tmp_path / "nope.md", tmp_vault)
