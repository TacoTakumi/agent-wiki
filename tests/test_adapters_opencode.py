import json
import sqlite3
from pathlib import Path

import pytest

from agent_wiki.adapters.opencode import (
    OpencodeAdapter,
    OpencodeSessionRef,
    _convert_session,
    _render_parts,
)


SCHEMA = """
CREATE TABLE session (
    id text PRIMARY KEY,
    project_id text,
    slug text,
    directory text NOT NULL,
    title text NOT NULL,
    version text,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    time_archived integer
);
CREATE TABLE message (
    id text PRIMARY KEY,
    session_id text NOT NULL,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    data text NOT NULL
);
CREATE TABLE part (
    id text PRIMARY KEY,
    message_id text NOT NULL,
    session_id text NOT NULL,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    data text NOT NULL
);
"""


def _build_db(tmp_path: Path, sessions, messages, parts) -> Path:
    db = tmp_path / "oc.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for s in sessions:
        conn.execute(
            "INSERT INTO session (id, directory, title, time_created, time_updated, time_archived) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (s["id"], s.get("directory", ""), s["title"],
             s["time_created"], s["time_updated"], s.get("time_archived")),
        )
    for i, m in enumerate(messages):
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (m["id"], m["session_id"], m["time_created"], m["time_created"],
             json.dumps(m["data"])),
        )
    for i, p in enumerate(parts):
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"part_{i}", p["message_id"], p["session_id"],
             p["time_created"], p["time_created"], json.dumps(p["data"])),
        )
    conn.commit()
    conn.close()
    return db


def _fixture_session(tmp_path):
    sessions = [{
        "id": "ses_1",
        "directory": "/home/user/AI/Projects/SampleApp",
        "title": "Review project files",
        "time_created": 1_776_519_415_472,
        "time_updated": 1_776_519_543_395,
    }]
    messages = [
        {"id": "msg_u1", "session_id": "ses_1", "time_created": 1_776_519_415_497,
         "data": {"role": "user", "time": {"created": 1_776_519_415_497}}},
        {"id": "msg_a1", "session_id": "ses_1", "time_created": 1_776_519_543_395,
         "data": {"role": "assistant", "time": {"created": 1_776_519_543_395},
                  "modelID": "Qwen3.6-35B-A3B-Q8",
                  "tokens": {"input": 20680, "output": 83}}},
    ]
    parts = [
        {"message_id": "msg_u1", "session_id": "ses_1", "time_created": 1_776_519_415_497,
         "data": {"type": "text", "text": "Please read the files."}},
        {"message_id": "msg_a1", "session_id": "ses_1", "time_created": 1_776_519_541_000,
         "data": {"type": "reasoning", "text": "secret chain of thought"}},
        {"message_id": "msg_a1", "session_id": "ses_1", "time_created": 1_776_519_543_300,
         "data": {"type": "tool", "tool": "read",
                  "state": {"input": {"filePath": "/x"}, "output": "hello"}}},
        {"message_id": "msg_a1", "session_id": "ses_1", "time_created": 1_776_519_543_305,
         "data": {"type": "step-finish", "reason": "tool-calls"}},
    ]
    return _build_db(tmp_path, sessions, messages, parts)


def test_convert_session_renders_turns(tmp_path):
    db = _fixture_session(tmp_path)
    adapter = OpencodeAdapter({"db_path": str(db), "include_live": True})
    refs = list(adapter.discover())
    assert len(refs) == 1
    conv = adapter.to_bundle(refs[0])

    assert conv.agent == "opencode"
    assert conv.session_id == "ses_1"
    assert conv.title == "Review project files"
    assert conv.project == "SampleApp"
    assert conv.model == "Qwen3.6-35B-A3B-Q8"
    assert conv.turns == 2
    assert conv.token_totals == {"input": 20680, "output": 83}
    assert conv.tool_counts == {"read": 1}
    assert "secret chain of thought" not in conv.body
    assert "Please read the files." in conv.body
    assert "tool: `read`" in conv.body or "**tool:** `read`" in conv.body
    assert "hello" in conv.body


def test_discover_skips_live(tmp_path):
    # mtime = now (in ms)
    import time
    now_ms = int(time.time() * 1000)
    sessions = [{
        "id": "ses_live",
        "directory": "/tmp/proj",
        "title": "Live session",
        "time_created": now_ms - 1000,
        "time_updated": now_ms,
    }]
    db = _build_db(tmp_path, sessions, [], [])
    adapter = OpencodeAdapter({"db_path": str(db)})
    assert list(adapter.discover()) == []
    adapter_live = OpencodeAdapter({"db_path": str(db), "include_live": True})
    assert [r.id for r in adapter_live.discover()] == ["ses_live"]


def test_fingerprint_uses_time_updated(tmp_path):
    ref = OpencodeSessionRef(id="x", time_updated=42, title="", directory="")
    adapter = OpencodeAdapter({"db_path": str(tmp_path / "nope.db")})
    assert adapter.fingerprint(ref) == "time_updated:42"


def test_render_parts_handles_unknown_type():
    out, tools = _render_parts([{"type": "mystery-meat"}])
    assert "mystery-meat" in out
    assert tools == []


def test_missing_db_is_quiet(tmp_path):
    adapter = OpencodeAdapter({"db_path": str(tmp_path / "does-not-exist.db")})
    assert list(adapter.discover()) == []


def test_session_key_from_row_id_without_opening_db(tmp_path, monkeypatch):
    db = _fixture_session(tmp_path)
    adapter = OpencodeAdapter({"db_path": str(db), "include_live": True})
    refs = list(adapter.discover())
    conv = adapter.to_bundle(refs[0])

    def _no_connect(*args, **kwargs):
        raise AssertionError("session_key must not open the database")

    monkeypatch.setattr(sqlite3, "connect", _no_connect)
    key = adapter.session_key(refs[0])

    assert key == f"{conv.agent}:{conv.session_id}"
    assert key == "opencode:ses_1"


# --- DB path resolution: explicit config > `opencode db path` > historical default

import os
import stat

from agent_wiki.adapters.opencode import DEFAULT_DB


def _fake_opencode(tmp_path, monkeypatch, script: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "opencode"
    exe.write_text("#!/bin/sh\n" + script + "\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))
    return exe


def test_db_path_explicit_config_wins(tmp_path, monkeypatch):
    _fake_opencode(tmp_path, monkeypatch, 'echo /elsewhere/custom.db')
    adapter = OpencodeAdapter({"db_path": str(tmp_path / "mine.db")})
    assert adapter.db_path == tmp_path / "mine.db"


def test_db_path_from_opencode_db_path_subcommand(tmp_path, monkeypatch):
    custom = tmp_path / "opencode-prod.db"
    _fake_opencode(tmp_path, monkeypatch,
                   f'[ "$1" = "db" ] && [ "$2" = "path" ] && echo "{custom}" && exit 0; exit 2')
    adapter = OpencodeAdapter({})
    assert adapter.db_path == custom


def test_db_path_default_when_no_binary(tmp_path, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert OpencodeAdapter({}).db_path == DEFAULT_DB


def test_db_path_default_when_subcommand_fails(tmp_path, monkeypatch):
    _fake_opencode(tmp_path, monkeypatch, 'echo "unknown command" >&2; exit 1')
    assert OpencodeAdapter({}).db_path == DEFAULT_DB
