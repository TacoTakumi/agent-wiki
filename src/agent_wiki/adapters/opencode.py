"""Opencode adapter.

Opencode stores sessions in SQLite at ``~/.local/share/opencode/opencode.db``
(with a JSON file mirror under ``storage/`` that we don't use — the DB is
authoritative). Relevant schema:

- ``session`` — one row per conversation: ``id``, ``title``, ``directory``,
  ``time_created``, ``time_updated``, ``time_archived``.
- ``message`` — per-turn metadata (``data`` is a JSON blob with ``role``,
  ``time``, ``model``, ``tokens``).
- ``part`` — content chunks within a message. ``data`` is JSON with a
  discriminator ``type``: ``text``, ``reasoning``, ``tool``,
  ``step-start``, ``step-finish``, ``snapshot``, …

The adapter opens the DB read-only (``?mode=ro&immutable=1``) so it is safe
to run against a live Opencode session.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_wiki.adapters import ConversationAdapter
from agent_wiki.conversation import Conversation

DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
LIVE_THRESHOLD = timedelta(minutes=60)

TOOL_RESULT_MAX_CHARS = 500


@dataclass
class OpencodeSessionRef:
    """Opaque reference used internally between discover/fingerprint/to_bundle."""

    id: str
    time_updated: int  # ms since epoch
    title: str
    directory: str

    def __str__(self) -> str:  # used for error messages
        return f"opencode:{self.id}"


class OpencodeAdapter(ConversationAdapter):
    name = "opencode"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        db_path = self.config.get("db_path")
        self.db_path = Path(db_path).expanduser() if db_path else DEFAULT_DB
        self.include_live = bool(self.config.get("include_live", False))
        self.since: datetime | None = None

    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"opencode DB not found: {self.db_path}")
        uri = f"file:{self.db_path}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def discover(self) -> Iterable[OpencodeSessionRef]:
        try:
            conn = self._connect()
        except FileNotFoundError:
            return
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        live_cutoff_ms = now_ms - int(LIVE_THRESHOLD.total_seconds() * 1000)
        since_ms = int(self.since.timestamp() * 1000) if self.since else None

        rows = conn.execute(
            "SELECT id, title, directory, time_updated, time_archived "
            "FROM session ORDER BY time_updated ASC"
        ).fetchall()
        conn.close()

        for r in rows:
            tu = int(r["time_updated"] or 0)
            if not self.include_live and tu > live_cutoff_ms:
                continue
            if since_ms is not None and tu < since_ms:
                continue
            yield OpencodeSessionRef(
                id=r["id"],
                time_updated=tu,
                title=r["title"] or r["id"],
                directory=r["directory"] or "",
            )

    def fingerprint(self, ref: OpencodeSessionRef) -> str:
        return f"time_updated:{ref.time_updated}"

    def to_bundle(self, ref: OpencodeSessionRef) -> Conversation:
        return _convert_session(self._connect(), ref)


# ---------------------------------------------------------------------------
# Conversion (conn-in parameter so tests can inject an in-memory DB)
# ---------------------------------------------------------------------------


def _convert_session(conn: sqlite3.Connection, ref: OpencodeSessionRef) -> Conversation:
    try:
        conn.row_factory = sqlite3.Row
        session_row = conn.execute(
            "SELECT id, title, directory, time_created, time_updated "
            "FROM session WHERE id = ?",
            (ref.id,),
        ).fetchone()
        if session_row is None:
            raise KeyError(f"session not found: {ref.id}")

        messages = conn.execute(
            "SELECT id, data, time_created FROM message "
            "WHERE session_id = ? ORDER BY time_created ASC",
            (ref.id,),
        ).fetchall()
        parts = conn.execute(
            "SELECT message_id, data, time_created FROM part "
            "WHERE session_id = ? ORDER BY time_created ASC",
            (ref.id,),
        ).fetchall()
    finally:
        conn.close()

    # Group parts by message_id, preserving order
    parts_by_message: dict[str, list[dict]] = {}
    for row in parts:
        try:
            payload = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        parts_by_message.setdefault(row["message_id"], []).append(payload)

    tool_counts: dict[str, int] = {}
    input_tokens = 0
    output_tokens = 0
    model: str | None = None
    sections: list[str] = []
    turns = 0

    started_ms: int | None = None
    ended_ms: int | None = None

    for msg in messages:
        try:
            meta = json.loads(msg["data"])
        except (TypeError, json.JSONDecodeError):
            meta = {}

        role = meta.get("role") or "unknown"
        ts_ms = (meta.get("time") or {}).get("created") or msg["time_created"]
        if ts_ms:
            if started_ms is None or ts_ms < started_ms:
                started_ms = ts_ms
            if ended_ms is None or ts_ms > ended_ms:
                ended_ms = ts_ms

        if role == "assistant":
            if not model:
                model = meta.get("modelID") or (meta.get("model") or {}).get("modelID")
            tokens = meta.get("tokens") or {}
            input_tokens += int(tokens.get("input") or 0)
            output_tokens += int(tokens.get("output") or 0)

        rendered, tools_used = _render_parts(parts_by_message.get(msg["id"], []))
        for t in tools_used:
            tool_counts[t] = tool_counts.get(t, 0) + 1

        if not rendered:
            continue

        ts_str = ""
        if ts_ms:
            ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")
        header = f"## [{ts_str}] {role}" if ts_str else f"## {role}"
        sections.append(f"{header}\n\n{rendered}")
        turns += 1

    started = datetime.fromtimestamp(started_ms / 1000, tz=timezone.utc) if started_ms else None
    ended = datetime.fromtimestamp(ended_ms / 1000, tz=timezone.utc) if ended_ms else None

    directory = session_row["directory"] or ref.directory
    project = Path(directory).name if directory else None

    body = "\n\n".join(sections).rstrip() + "\n"

    return Conversation(
        agent="opencode",
        session_id=ref.id,
        title=session_row["title"] or ref.title,
        body=body,
        project=project,
        started=started,
        ended=ended,
        model=model,
        turns=turns,
        tool_counts=tool_counts,
        token_totals={"input": input_tokens, "output": output_tokens} if (input_tokens or output_tokens) else {},
    )


def _render_parts(parts: list[dict]) -> tuple[str, list[str]]:
    pieces: list[str] = []
    tools: list[str] = []
    for p in parts:
        pt = p.get("type")
        if pt == "text":
            txt = (p.get("text") or "").strip()
            if txt:
                pieces.append(txt)
        elif pt == "reasoning":
            # Drop — model-internal chain-of-thought, like Claude "thinking".
            continue
        elif pt == "tool":
            tool_name = p.get("tool") or "tool"
            tools.append(tool_name)
            state = p.get("state") or {}
            inp = state.get("input") or {}
            summary = _summarize_tool_input(tool_name, inp)
            output = _truncate(str(state.get("output") or ""), TOOL_RESULT_MAX_CHARS)
            block = f"**tool:** `{tool_name}` — {summary}"
            if output:
                block += f"\n\n```\n{output}\n```"
            pieces.append(block)
        elif pt in ("step-start", "step-finish", "snapshot"):
            continue
        else:
            # Unknown part type: preserve the type label so we notice if schema changes.
            pieces.append(f"*[unhandled part type: {pt}]*")
    return "\n\n".join(x for x in pieces if x).strip(), tools


def _summarize_tool_input(name: str, inp: dict) -> str:
    if not inp:
        return ""
    if name in ("bash", "Bash"):
        cmd = (inp.get("command") or "").splitlines()
        return f"`{cmd[0][:160]}`" if cmd else ""
    if name in ("read", "write", "edit"):
        return inp.get("filePath") or inp.get("file_path") or ""
    if name in ("grep", "glob"):
        return f"pattern={inp.get('pattern','')!r}"
    for v in inp.values():
        if isinstance(v, str):
            return v[:160]
    return ""


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n… [truncated {len(text)-max_chars} chars]"
