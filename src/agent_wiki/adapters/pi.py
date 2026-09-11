"""pi adapter.

Reads JSONL session files from ``~/.pi/agent/sessions/<cwd-slug>/<ISO>_<uuid>.jsonl``
(pi's ``docs/session-format.md``, session version 3).

The first line is a ``session`` header (``id``, ``cwd``, ``version``). Every
later line is a tree entry with ``id``/``parentId``; we treat one file as one
linear session and ignore branch structure. Entries we care about:

- ``message`` — the turns. ``message.role`` is ``user``, ``assistant``,
  ``toolResult`` or ``bashExecution`` (``custom``, ``branchSummary`` and
  ``compactionSummary`` are skipped).
- ``model_change`` — the last one names the session's model.
- ``session_info`` — user-set display name, used as the title.

Assistant content blocks are ``text``, ``thinking`` (dropped) and ``toolCall``
(counted and summarised). Tool output arrives as a separate ``toolResult``
message, which we render truncated like the Claude Code adapter does.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_wiki.adapters import ConversationAdapter
from agent_wiki.conversation import Conversation

DEFAULT_ROOT = Path.home() / ".pi" / "agent" / "sessions"
LIVE_THRESHOLD = timedelta(minutes=60)

TOOL_RESULT_MAX_CHARS = 500


def session_id_from_filename(path: Path) -> str:
    """``<ISO>_<uuid>.jsonl`` -> ``<uuid>``; a stem without ``_`` is used whole."""
    stem = path.stem
    if "_" in stem:
        return stem.split("_", 1)[1]
    return stem


class PiAdapter(ConversationAdapter):
    name = "pi"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        path = self.config.get("path")
        self.root = Path(path).expanduser() if path else DEFAULT_ROOT
        self.include_live = bool(self.config.get("include_live", False))
        self.since: datetime | None = None

    def discover(self) -> Iterable[Path]:
        if not self.root.exists():
            return
        now = datetime.now(timezone.utc)
        for jsonl in sorted(self.root.rglob("*.jsonl")):
            try:
                mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if not self.include_live and now - mtime < LIVE_THRESHOLD:
                continue
            if self.since and mtime < self.since:
                continue
            yield jsonl

    def session_key(self, ref: Path) -> str:
        # The filename carries the header uuid, so the key needs no read.
        return f"{self.name}:{session_id_from_filename(ref)}"

    def fingerprint(self, ref: Path) -> str:
        st = ref.stat()
        return f"mtime:{int(st.st_mtime)}:size:{st.st_size}"

    def to_bundle(self, ref: Path) -> Conversation:
        return convert_jsonl(ref)


# ---------------------------------------------------------------------------
# Conversion (pure function so it's easy to test without a real session dir)
# ---------------------------------------------------------------------------


def convert_jsonl(path: Path) -> Conversation:
    """Convert a pi session JSONL file to a Conversation bundle."""
    session_id: str | None = None
    cwd: str | None = None
    session_name: str | None = None
    model: str | None = None
    fallback_model: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    tool_counts: Counter[str] = Counter()
    input_tokens = 0
    output_tokens = 0
    turns = 0
    sections: list[str] = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue

            rtype = rec.get("type")

            if rtype == "session":
                session_id = session_id or rec.get("id")
                cwd = cwd or rec.get("cwd")
                continue
            if rtype == "model_change":
                model = rec.get("modelId") or model
                continue
            if rtype == "session_info":
                session_name = rec.get("name") or session_name
                continue
            if rtype != "message":
                continue

            msg = rec.get("message") or {}
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or "unknown"

            ts = _parse_ts(rec.get("timestamp"))
            if ts:
                if started is None or ts < started:
                    started = ts
                if ended is None or ts > ended:
                    ended = ts

            if role == "assistant":
                fallback_model = fallback_model or msg.get("model")
                usage = msg.get("usage") or {}
                input_tokens += int(usage.get("input") or 0)
                output_tokens += int(usage.get("output") or 0)

            rendered, used_tools = _render_message(role, msg)
            for t in used_tools:
                tool_counts[t] += 1
            if not rendered:
                continue

            ts_str = ts.strftime("%H:%M:%S") if ts else ""
            header = f"## [{ts_str}] {role}" if ts_str else f"## {role}"
            sections.append(f"{header}\n\n{rendered}")
            turns += 1

    if not session_id:
        session_id = session_id_from_filename(path)

    project = Path(cwd).name if cwd else None
    title = session_name or _derive_title(sections) or path.stem

    body = "\n\n".join(sections).rstrip() + "\n"

    return Conversation(
        agent="pi",
        session_id=session_id,
        title=title,
        body=body,
        project=project,
        started=started,
        ended=ended,
        model=model or fallback_model,
        turns=turns,
        tool_counts=dict(tool_counts),
        token_totals={"input": input_tokens, "output": output_tokens} if (input_tokens or output_tokens) else {},
    )


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _render_message(role: str, msg: dict) -> tuple[str, list[str]]:
    """Return (rendered_text, tool_names_used) for one message."""
    if role in ("user", "assistant"):
        return _render_blocks(msg.get("content"))
    if role == "toolResult":
        name = msg.get("toolName") or "tool"
        text, _ = _render_blocks(msg.get("content"))
        text = _truncate(text, TOOL_RESULT_MAX_CHARS)
        label = "tool_result (error)" if msg.get("isError") else "tool_result"
        return (f"**{label}:** `{name}`\n\n{text}" if text else f"**{label}:** `{name}`"), []
    if role == "bashExecution":
        cmd = (msg.get("command") or "").strip()
        out = _truncate(str(msg.get("output") or ""), TOOL_RESULT_MAX_CHARS)
        block = f"**bash:** `{cmd[:160]}`"
        if out:
            block += f"\n\n```\n{out}\n```"
        return block, []
    # custom, branchSummary, compactionSummary and anything unknown.
    return "", []


def _render_blocks(content: Any) -> tuple[str, list[str]]:
    if content is None:
        return "", []
    if isinstance(content, str):
        return content.strip(), []
    if not isinstance(content, list):
        return "", []

    pieces: list[str] = []
    tools: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt == "text":
            txt = (block.get("text") or "").strip()
            if txt:
                pieces.append(txt)
        elif bt == "thinking":
            continue
        elif bt == "toolCall":
            name = block.get("name") or "tool"
            tools.append(name)
            summary = _summarize_tool_call(name, block.get("arguments") or {})
            pieces.append(f"**tool_use:** `{name}` — {summary}")
        elif bt == "image":
            pieces.append("[image]")
    return "\n\n".join(p for p in pieces if p).strip(), tools


def _summarize_tool_call(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    if name == "bash":
        cmd = (args.get("command") or "").splitlines()
        return f"`{cmd[0][:160]}`" if cmd else ""
    if name in ("read", "write", "edit"):
        return args.get("path") or args.get("file_path") or ""
    if name in ("grep", "find", "ls"):
        return args.get("pattern") or args.get("path") or ""
    for v in args.values():
        if isinstance(v, str):
            return v[:160]
    return ""


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n… [truncated {len(text)-max_chars} chars]"


def _derive_title(sections: list[str]) -> str | None:
    # First user message, first line, trimmed.
    for s in sections:
        if s.startswith("## ") and " user" in s.splitlines()[0]:
            lines = [l for l in s.splitlines()[1:] if l.strip()]
            if lines:
                first = lines[0].lstrip("#").strip()
                return first[:80] if first else None
    return None
