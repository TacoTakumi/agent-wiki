"""Claude Code adapter.

Reads JSONL session transcripts from ``~/.claude/projects/<slug>/*.jsonl``.

Each line is a JSON record with a ``type`` field. We care about:

- ``user`` / ``assistant`` — the turns themselves.
- ``custom-title`` — human-set session title, if present.

User ``message.content`` is either a string (direct user input) or a list of
blocks (``text``, ``tool_result``). Assistant ``message.content`` is always a
list of blocks (``text``, ``thinking``, ``tool_use``).

We transcribe one section per turn, collapse tool_use / tool_result bodies,
drop ``thinking`` blocks (they're internal to the model), and skip non-turn
record types like ``permission-mode``, ``file-history-snapshot``, etc.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_wiki.adapters import ConversationAdapter
from agent_wiki.conversation import Conversation

DEFAULT_ROOT = Path.home() / ".claude" / "projects"
LIVE_THRESHOLD = timedelta(minutes=60)

# Directory-name → project slug. Claude Code encodes cwd paths like:
#   /home/user/AI/Projects/agent-wiki  →  -home-user-AI-Projects-agent-wiki
# The reverse is lossy (slashes vs dashes) so we just use the trailing segment
# as the friendly project slug.
_PROJECT_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _friendly_project(encoded_dir: str) -> str:
    # Keep the trailing alphanumeric segment as the human-readable project name.
    parts = [p for p in encoded_dir.split("-") if p]
    return parts[-1] if parts else encoded_dir


class ClaudeCodeAdapter(ConversationAdapter):
    name = "claude-code"

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

    def fingerprint(self, ref: Path) -> str:
        return f"mtime:{int(ref.stat().st_mtime)}:size:{ref.stat().st_size}"

    def to_bundle(self, ref: Path) -> Conversation:
        return convert_jsonl(ref)


# ---------------------------------------------------------------------------
# Conversion (pure function so it's easy to test without a real session dir)
# ---------------------------------------------------------------------------


def convert_jsonl(path: Path) -> Conversation:
    """Convert a Claude Code JSONL transcript to a Conversation bundle."""
    session_id: str | None = None
    custom_title: str | None = None
    project: str | None = None
    cwd: str | None = None
    model: str | None = None
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

            rtype = rec.get("type")

            if rtype == "custom-title":
                custom_title = rec.get("customTitle") or custom_title

            if not session_id:
                session_id = rec.get("sessionId")
            if not cwd:
                cwd = rec.get("cwd")

            if rtype not in ("user", "assistant"):
                continue

            ts = _parse_ts(rec.get("timestamp"))
            if ts:
                started = started if started and started < ts else (started or ts)
                ended = ts

            msg = rec.get("message") or {}
            role = msg.get("role") or rtype
            content = msg.get("content")

            if role == "assistant" and not model:
                model = msg.get("model")

            if role == "assistant":
                usage = msg.get("usage") or {}
                input_tokens += int(usage.get("input_tokens") or 0)
                output_tokens += int(usage.get("output_tokens") or 0)

            rendered, used_tools = _render_blocks(content)
            for t in used_tools:
                tool_counts[t] += 1

            if not rendered:
                continue

            ts_str = ts.strftime("%H:%M:%S") if ts else ""
            header = f"## [{ts_str}] {role}" if ts_str else f"## {role}"
            sections.append(f"{header}\n\n{rendered}")
            turns += 1

    if not session_id:
        session_id = path.stem

    if cwd:
        project = Path(cwd).name
    elif path.parent != path:
        project = _friendly_project(path.parent.name)

    title = custom_title or _derive_title(sections) or (project or session_id)

    body = "\n\n".join(sections).rstrip() + "\n"

    return Conversation(
        agent="claude-code",
        session_id=session_id,
        title=title,
        body=body,
        project=project,
        started=started,
        ended=ended,
        model=model,
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


def _render_blocks(content: Any) -> tuple[str, list[str]]:
    """Return (rendered_text, tool_names_used)."""
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
        elif bt == "tool_use":
            name = block.get("name") or "tool"
            tools.append(name)
            summary = _summarize_tool_use(name, block.get("input") or {})
            pieces.append(f"**tool_use:** `{name}` — {summary}")
        elif bt == "tool_result":
            txt = _summarize_tool_result(block.get("content"))
            if txt:
                pieces.append(f"**tool_result:**\n\n{txt}")
    return "\n\n".join(p for p in pieces if p).strip(), tools


def _summarize_tool_use(name: str, inp: dict) -> str:
    # Short, scannable summaries. Full details live in the raw JSONL if ever needed.
    if name == "Bash":
        cmd = (inp.get("command") or "").splitlines()
        return f"`{cmd[0][:160]}`" if cmd else ""
    if name == "Read":
        return inp.get("file_path", "")
    if name == "Write":
        return inp.get("file_path", "")
    if name == "Edit":
        return inp.get("file_path", "")
    if name == "Grep":
        return f"pattern={inp.get('pattern','')!r}"
    if name == "Glob":
        return f"pattern={inp.get('pattern','')!r}"
    # Fallback: first scalar value, truncated
    for v in inp.values():
        if isinstance(v, str):
            return v[:160]
    return ""


def _summarize_tool_result(content: Any, max_chars: int = 500) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _truncate(content, max_chars)
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                elif b.get("type") == "image":
                    parts.append("[image]")
            else:
                parts.append(str(b))
        return _truncate("\n".join(parts), max_chars)
    return _truncate(str(content), max_chars)


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
