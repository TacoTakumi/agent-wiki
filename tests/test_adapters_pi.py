import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_wiki.adapters.pi import PiAdapter, convert_jsonl


SESSION_ID = "019fd2a9-7e14-787f-a8e2-178a5399e40e"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _header(session_id: str = SESSION_ID, cwd: str = "/home/user/AI/Projects/herdr") -> dict:
    return {"type": "session", "version": 3, "id": session_id,
            "timestamp": "2026-08-05T16:02:31.060Z", "cwd": cwd}


def _model_change(entry_id: str, parent: str | None, ts: str, model: str) -> dict:
    return {"type": "model_change", "id": entry_id, "parentId": parent, "timestamp": ts,
            "provider": "openrouter", "modelId": model}


def _user(entry_id: str, parent: str, ts: str, text: str) -> dict:
    return {"type": "message", "id": entry_id, "parentId": parent, "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "text", "text": text}],
                        "timestamp": 1}}


def _assistant(entry_id: str, parent: str, ts: str, blocks: list[dict], model: str = "m") -> dict:
    return {"type": "message", "id": entry_id, "parentId": parent, "timestamp": ts,
            "message": {"role": "assistant", "content": blocks, "api": "x",
                        "provider": "openrouter", "model": model,
                        "usage": {"input": 100, "output": 20},
                        "stopReason": "stop", "timestamp": 1}}


def _tool_result(entry_id: str, parent: str, ts: str, text: str) -> dict:
    return {"type": "message", "id": entry_id, "parentId": parent, "timestamp": ts,
            "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "bash",
                        "content": [{"type": "text", "text": text}], "isError": False,
                        "timestamp": 1}}


def _fixture_records() -> list[dict]:
    return [
        _header(),
        _model_change("e1", None, "2026-08-05T16:02:39.119Z", "deepseek/deepseek-v4-flash"),
        {"type": "thinking_level_change", "id": "e2", "parentId": "e1",
         "timestamp": "2026-08-05T16:02:39.119Z", "thinkingLevel": "high"},
        _user("e3", "e2", "2026-08-05T16:04:46.957Z", "Fix the remote flag please"),
        _assistant("e4", "e3", "2026-08-05T16:05:04.352Z", [
            {"type": "thinking", "thinking": "secret chain of thought"},
            {"type": "text", "text": "Looking now."},
            {"type": "toolCall", "id": "c1", "name": "bash",
             "arguments": {"command": "rg -n remote src/"}},
        ]),
        _tool_result("e5", "e4", "2026-08-05T16:05:05.000Z", "src/remote.ts:12: remote"),
        _model_change("e6", "e5", "2026-08-05T16:06:00.000Z", "anthropic/claude-opus-5"),
        _assistant("e7", "e6", "2026-08-05T16:07:00.000Z", [
            {"type": "text", "text": "Found it in remote.ts."},
        ], model="anthropic/claude-opus-5"),
    ]


def test_convert_v3_session(tmp_path):
    jsonl = tmp_path / "--home-user-AI-Projects-herdr--" / f"2026-08-05T16-02-31-060Z_{SESSION_ID}.jsonl"
    _write_jsonl(jsonl, _fixture_records())

    conv = convert_jsonl(jsonl)

    assert conv.agent == "pi"
    assert conv.session_id == SESSION_ID
    assert conv.title == "Fix the remote flag please"
    assert conv.project == "herdr"
    assert conv.model == "anthropic/claude-opus-5"
    assert conv.started == datetime(2026, 8, 5, 16, 4, 46, 957000, tzinfo=timezone.utc)
    assert conv.ended == datetime(2026, 8, 5, 16, 7, 0, tzinfo=timezone.utc)
    assert conv.turns == 4
    assert conv.tool_counts == {"bash": 1}
    assert conv.token_totals == {"input": 200, "output": 40}
    assert "secret chain of thought" not in conv.body
    assert "Fix the remote flag please" in conv.body
    assert "`bash`" in conv.body
    assert "src/remote.ts:12" in conv.body


def test_convert_title_falls_back_to_stem_and_session_name_wins(tmp_path):
    stem = f"2026-08-05T16-02-31-060Z_{SESSION_ID}"
    jsonl = tmp_path / "p" / f"{stem}.jsonl"
    _write_jsonl(jsonl, [_header()])
    conv = convert_jsonl(jsonl)
    assert conv.title == stem
    assert conv.turns == 0

    _write_jsonl(jsonl, _fixture_records() + [
        {"type": "session_info", "id": "e8", "parentId": "e7",
         "timestamp": "2026-08-05T16:08:00.000Z", "name": "Remote flag fix"},
    ])
    assert convert_jsonl(jsonl).title == "Remote flag fix"


def test_adapter_to_bundle_uses_convert(tmp_path):
    jsonl = tmp_path / "p" / f"2026-08-05T16-02-31-060Z_{SESSION_ID}.jsonl"
    _write_jsonl(jsonl, _fixture_records())
    adapter = PiAdapter({"path": str(tmp_path), "include_live": True})
    conv = adapter.to_bundle(jsonl)
    assert conv.agent == "pi"
    assert conv.session_id == SESSION_ID


def test_discover_skips_live_sessions_unless_include_live(tmp_path):
    import os
    import time

    root = tmp_path / "sessions"
    jsonl = root / "p" / f"2026-08-05T16-02-31-060Z_{SESSION_ID}.jsonl"
    _write_jsonl(jsonl, _fixture_records())

    assert list(PiAdapter({"path": str(root)}).discover()) == []
    assert list(PiAdapter({"path": str(root), "include_live": True}).discover()) == [jsonl]

    old = time.time() - 3600 * 2
    os.utime(jsonl, (old, old))
    assert list(PiAdapter({"path": str(root)}).discover()) == [jsonl]


def test_discover_respects_since(tmp_path):
    import os
    import time

    root = tmp_path / "sessions"
    jsonl = root / "p" / f"2026-08-05T16-02-31-060Z_{SESSION_ID}.jsonl"
    _write_jsonl(jsonl, _fixture_records())
    old = time.time() - 3600 * 48
    os.utime(jsonl, (old, old))

    adapter = PiAdapter({"path": str(root)})
    adapter.since = datetime.now(timezone.utc) - timedelta(hours=24)
    assert list(adapter.discover()) == []

    adapter.since = datetime.now(timezone.utc) - timedelta(hours=72)
    assert list(adapter.discover()) == [jsonl]


def test_missing_root_is_quiet(tmp_path):
    assert list(PiAdapter({"path": str(tmp_path / "nope")}).discover()) == []


def test_fingerprint_changes_on_mtime_change(tmp_path):
    import os
    import time

    jsonl = tmp_path / "p" / f"2026-08-05T16-02-31-060Z_{SESSION_ID}.jsonl"
    _write_jsonl(jsonl, _fixture_records())
    adapter = PiAdapter({"path": str(tmp_path)})
    fp1 = adapter.fingerprint(jsonl)
    t = time.time() - 10
    os.utime(jsonl, (t, t))
    assert adapter.fingerprint(jsonl) != fp1


def test_session_key_from_filename_without_opening_body(tmp_path, monkeypatch):
    import builtins

    jsonl = tmp_path / "p" / f"2026-08-05T16-02-31-060Z_{SESSION_ID}.jsonl"
    _write_jsonl(jsonl, _fixture_records())
    adapter = PiAdapter({"path": str(tmp_path), "include_live": True})
    conv = adapter.to_bundle(jsonl)

    real_open = builtins.open

    def _no_open(*args, **kwargs):
        raise AssertionError("session_key must not open the transcript")

    monkeypatch.setattr(builtins, "open", _no_open)
    try:
        key = adapter.session_key(jsonl)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert key == f"{conv.agent}:{conv.session_id}"
    assert key == f"pi:{SESSION_ID}"
