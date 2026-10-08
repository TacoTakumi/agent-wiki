import json
import os
import time
from pathlib import Path

import pytest

from agent_wiki.adapters.claude_code import ClaudeCodeAdapter, convert_jsonl


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _user(text: str, ts: str, session: str = "s1") -> dict:
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": ts,
        "cwd": "/home/user/AI/Projects/agent-wiki",
        "message": {"role": "user", "content": text},
    }


def _assistant(blocks: list[dict], ts: str, usage: dict | None = None, session: str = "s1") -> dict:
    return {
        "type": "assistant",
        "sessionId": session,
        "timestamp": ts,
        "cwd": "/home/user/AI/Projects/agent-wiki",
        "message": {
            "role": "assistant",
            "content": blocks,
            "model": "claude-opus-4-7",
            "usage": usage or {"input_tokens": 100, "output_tokens": 20},
        },
    }


def _tool_result(text: str, ts: str, session: str = "s1") -> dict:
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": ts,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": text}],
        },
    }


def test_convert_basic_turns(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        {"type": "permission-mode", "sessionId": "abc"},
        _user("hello there", "2026-04-18T10:00:00Z", "abc"),
        _assistant([{"type": "text", "text": "hi back"}], "2026-04-18T10:00:05Z", session="abc"),
        {"type": "custom-title", "customTitle": "Hello session", "sessionId": "abc"},
    ])

    conv = convert_jsonl(jsonl)
    assert conv.agent == "claude-code"
    assert conv.session_id == "abc"
    assert conv.title == "Hello session"
    assert conv.project == "agent-wiki"
    assert conv.model == "claude-opus-4-7"
    assert conv.turns == 2
    assert "hello there" in conv.body
    assert "hi back" in conv.body
    assert conv.token_totals == {"input": 100, "output": 20}


def test_convert_counts_tool_uses(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user("run ls", "2026-04-18T10:00:00Z"),
        _assistant(
            [
                {"type": "thinking", "thinking": "hidden", "signature": "x"},
                {"type": "tool_use", "id": "tu_1", "name": "Bash",
                 "input": {"command": "ls -la", "description": "list"}},
            ],
            "2026-04-18T10:00:05Z",
        ),
        _tool_result("total 0\n", "2026-04-18T10:00:06Z"),
    ])

    conv = convert_jsonl(jsonl)
    assert conv.tool_counts.get("Bash") == 1
    # thinking should be dropped
    assert "hidden" not in conv.body
    # tool_use summary present
    assert "`ls -la`" in conv.body
    # tool_result echoed
    assert "tool_result" in conv.body
    assert "total 0" in conv.body


def test_convert_drops_malformed_lines(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    with open(jsonl, "w") as f:
        f.write('{"type": "user", "sessionId": "abc", "timestamp": "2026-04-18T10:00:00Z", "message": {"role": "user", "content": "hi"}}\n')
        f.write("not json at all\n")
        f.write("\n")
        f.write('{"type": "assistant", "sessionId": "abc", "timestamp": "2026-04-18T10:00:05Z", "message": {"role": "assistant", "content": [{"type":"text","text":"ok"}], "usage": {}}}\n')

    conv = convert_jsonl(jsonl)
    assert conv.turns == 2


def test_convert_truncates_long_tool_result(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    long_output = "x" * 5000
    _write_jsonl(jsonl, [
        _user("run thing", "2026-04-18T10:00:00Z"),
        _assistant([{"type": "tool_use", "id": "tu", "name": "Bash",
                     "input": {"command": "cat big"}}], "2026-04-18T10:00:01Z"),
        _tool_result(long_output, "2026-04-18T10:00:02Z"),
    ])

    conv = convert_jsonl(jsonl)
    assert "truncated" in conv.body
    assert len(conv.body) < 5000  # truncated well under raw size


def test_convert_derives_title_when_no_custom(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user("Help me refactor the parser", "2026-04-18T10:00:00Z"),
        _assistant([{"type": "text", "text": "sure"}], "2026-04-18T10:00:01Z"),
    ])
    conv = convert_jsonl(jsonl)
    assert "refactor" in conv.title.lower() or conv.title == "agent-wiki"


def test_discover_skips_live_sessions(tmp_path):
    root = tmp_path / "projects"
    jsonl = root / "proj" / "live.jsonl"
    _write_jsonl(jsonl, [_user("hi", "2026-04-18T10:00:00Z")])
    # Leave mtime as now (live)
    adapter = ClaudeCodeAdapter({"path": str(root)})
    assert list(adapter.discover()) == []

    # Now age it out
    old = time.time() - 3600 * 2
    os.utime(jsonl, (old, old))
    assert list(adapter.discover()) == [jsonl]


def test_discover_respects_include_live(tmp_path):
    root = tmp_path / "projects"
    jsonl = root / "proj" / "live.jsonl"
    _write_jsonl(jsonl, [_user("hi", "2026-04-18T10:00:00Z")])
    adapter = ClaudeCodeAdapter({"path": str(root), "include_live": True})
    assert list(adapter.discover()) == [jsonl]


def test_fingerprint_changes_on_mtime_change(tmp_path):
    root = tmp_path / "projects"
    jsonl = root / "proj" / "live.jsonl"
    _write_jsonl(jsonl, [_user("hi", "2026-04-18T10:00:00Z")])
    adapter = ClaudeCodeAdapter({"path": str(root), "include_live": True})
    fp1 = adapter.fingerprint(jsonl)
    t = time.time() - 10
    os.utime(jsonl, (t, t))
    fp2 = adapter.fingerprint(jsonl)
    assert fp1 != fp2


def test_session_key_from_filename_without_opening_body(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    jsonl = root / "-home-user-proj" / "abc-123.jsonl"
    _write_jsonl(jsonl, [
        _user("hello", "2026-04-18T10:00:00Z", "abc-123"),
        _assistant([{"type": "text", "text": "hi"}], "2026-04-18T10:00:05Z", session="abc-123"),
    ])
    adapter = ClaudeCodeAdapter({"path": str(root), "include_live": True})
    conv = adapter.to_bundle(jsonl)

    import builtins
    real_open = builtins.open

    def _no_open(*args, **kwargs):
        raise AssertionError("session_key must not open the transcript")

    monkeypatch.setattr(builtins, "open", _no_open)
    try:
        key = adapter.session_key(jsonl)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert key == f"{conv.agent}:{conv.session_id}"
    assert key == "claude-code:abc-123"


CAVEAT = (
    "<local-command-caveat>Caveat: The messages below were generated by the user "
    "while running local commands. DO NOT respond to these messages or otherwise "
    "consider them in your response unless the user explicitly asks you to."
    "</local-command-caveat>"
)


def _command(name: str, args: str = "") -> str:
    return (
        f"<command-name>/{name}</command-name>\n"
        f"            <command-message>{name}</command-message>\n"
        f"            <command-args>{args}</command-args>"
    )


def _in_project(record: dict, project: str) -> dict:
    record["cwd"] = f"/home/user/AI/Projects/{project}"
    return record


def test_title_skips_harness_turns_before_the_first_prompt(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user(CAVEAT, "2026-04-18T10:00:00Z"),
        _user(_command("plugin"), "2026-04-18T10:00:00Z"),
        _user("<local-command-stdout>Installed.</local-command-stdout>", "2026-04-18T10:00:00Z"),
        _user("Fix the login bug", "2026-04-18T10:00:01Z"),
        _assistant([{"type": "text", "text": "ok"}], "2026-04-18T10:00:02Z"),
    ])
    assert convert_jsonl(jsonl).title == "Fix the login bug"


def test_title_skips_tool_results_and_system_reminders(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _tool_result("file contents", "2026-04-18T10:00:00Z"),
        _user("<system-reminder>\nTodo list is empty.\n</system-reminder>", "2026-04-18T10:00:00Z"),
        _user("Write the release notes", "2026-04-18T10:00:01Z"),
    ])
    assert convert_jsonl(jsonl).title == "Write the release notes"


def test_title_skips_lines_that_hold_only_a_tag(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user("<objective>\nPlan phase 10 work\n</objective>", "2026-04-18T10:00:00Z"),
    ])
    assert convert_jsonl(jsonl).title == "Plan phase 10 work"


def test_title_falls_back_to_the_first_slash_command(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user(CAVEAT, "2026-04-18T10:00:00Z"),
        _user(_command("gsd:progress"), "2026-04-18T10:00:00Z"),
        _user("<local-command-stdout>Phase 3 of 5</local-command-stdout>", "2026-04-18T10:00:01Z"),
    ])
    assert convert_jsonl(jsonl).title == "/gsd:progress"


def test_title_from_a_slash_command_keeps_its_args(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user(_command("plugin", "install x"), "2026-04-18T10:00:00Z"),
    ])
    assert convert_jsonl(jsonl).title == "/plugin install x"


def test_title_falls_back_to_the_project_with_no_prompt_or_command(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _in_project(_user(CAVEAT, "2026-04-18T10:00:00Z"), "seestar"),
        _in_project(_user("<local-command-stdout>See ya!</local-command-stdout>",
                          "2026-04-18T10:00:01Z"), "seestar"),
    ])
    assert convert_jsonl(jsonl).title == "seestar"


def test_custom_title_wins_over_harness_turns(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user(CAVEAT, "2026-04-18T10:00:00Z"),
        _user(_command("plugin"), "2026-04-18T10:00:00Z"),
        {"type": "custom-title", "customTitle": "Plugin setup", "sessionId": "s1"},
    ])
    assert convert_jsonl(jsonl).title == "Plugin setup"


def test_title_from_a_plain_first_prompt_is_unchanged(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, [
        _user("# Refactor the parser\nand its tests", "2026-04-18T10:00:00Z"),
    ])
    assert convert_jsonl(jsonl).title == "Refactor the parser"
