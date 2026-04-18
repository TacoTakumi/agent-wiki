"""Conversation bundle: canonical format for ingesting agent conversations.

A bundle is a single markdown file with YAML frontmatter stored under
``<vault>/raw/sessions/<agent>-<session_id>.md``. It is the seam between
agent-specific adapters and the wiki ingest pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from agent_wiki.config import load_vault_config
from agent_wiki.log import append_log
from agent_wiki.page import parse_page, render_page, slugify


BUNDLE_SUBDIR = "raw/sessions"


@dataclass
class Conversation:
    """Canonical conversation bundle.

    Attributes mirror the frontmatter schema documented in
    ``Doc/conversation-bundle-schema.md``.
    """

    agent: str
    session_id: str
    title: str
    body: str
    project: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    model: str | None = None
    turns: int | None = None
    tool_counts: dict[str, int] = field(default_factory=dict)
    token_totals: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def bundle_id(self) -> str:
        """Stable filename-safe identifier for this bundle."""
        return f"{slugify(self.agent)}-{slugify(self.session_id)}"

    def frontmatter(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "type": "conversation",
            "agent": self.agent,
            "session_id": self.session_id,
            "title": self.title,
        }
        if self.project:
            meta["project"] = self.project
        if self.started:
            meta["started"] = _iso(self.started)
        if self.ended:
            meta["ended"] = _iso(self.ended)
        if self.model:
            meta["model"] = self.model
        if self.turns is not None:
            meta["turns"] = self.turns
        if self.tool_counts:
            meta["tool_counts"] = dict(self.tool_counts)
        if self.token_totals:
            meta["token_totals"] = dict(self.token_totals)
        for k, v in self.extra.items():
            meta.setdefault(k, v)
        return meta


def _iso(value: datetime | date | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return value.isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def write_bundle(conv: Conversation, vault_path: Path) -> Path:
    """Write a Conversation as a bundle under raw/sessions/. Returns path."""
    bundles_dir = vault_path / BUNDLE_SUBDIR
    bundles_dir.mkdir(parents=True, exist_ok=True)
    path = bundles_dir / f"{conv.bundle_id()}.md"
    content = render_page(conv.frontmatter(), conv.body)
    path.write_text(content)
    return path


def read_bundle(path: Path) -> Conversation:
    """Load a bundle markdown file into a Conversation."""
    parsed = parse_page(path)
    meta = parsed["meta"] or {}
    body = parsed["body"]

    if meta.get("type") != "conversation":
        raise ValueError(
            f"{path}: not a conversation bundle (type={meta.get('type')!r})"
        )

    known = {
        "type", "agent", "session_id", "title", "project",
        "started", "ended", "model", "turns", "tool_counts", "token_totals",
    }
    extra = {k: v for k, v in meta.items() if k not in known}

    missing = [k for k in ("agent", "session_id", "title") if not meta.get(k)]
    if missing:
        raise ValueError(
            f"{path}: bundle missing required frontmatter: {', '.join(missing)}"
        )

    return Conversation(
        agent=str(meta["agent"]),
        session_id=str(meta["session_id"]),
        title=str(meta["title"]),
        body=body,
        project=meta.get("project"),
        started=_parse_dt(meta.get("started")),
        ended=_parse_dt(meta.get("ended")),
        model=meta.get("model"),
        turns=meta.get("turns"),
        tool_counts=dict(meta.get("tool_counts") or {}),
        token_totals=dict(meta.get("token_totals") or {}),
        extra=extra,
    )


def ingest_conversation(
    bundle_path: Path,
    vault_path: Path,
    summarizer: "Summarizer | None" = None,
    redactor: "Redactor | None" = None,
) -> Path:
    """Ingest a bundle into the vault as a wiki page.

    The bundle is expected to already live under raw/sessions/ (callers who
    hold an external bundle should copy it in first). Creates a page under
    the configured conversations topic and appends to log.md.

    Returns the path to the created wiki page.
    """
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")

    conv = read_bundle(bundle_path)

    if redactor is not None:
        conv.body = redactor.redact(conv.body)
        conv.title = redactor.redact(conv.title)

    vault_config = load_vault_config(vault_path)
    topic = (vault_config.get("conversations") or {}).get("topic", "sessions")
    topic_dir = vault_path / topic
    topic_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()

    # Rebuild bundle relative path for sources list
    try:
        bundle_rel = str(bundle_path.relative_to(vault_path))
    except ValueError:
        bundle_rel = f"{BUNDLE_SUBDIR}/{bundle_path.name}"

    meta: dict[str, Any] = {
        "title": conv.title,
        "topic": topic,
        "type": "conversation",
        "agent": conv.agent,
        "session_id": conv.session_id,
        "tags": [conv.agent],
        "created": today,
        "updated": today,
        "sources": [bundle_rel],
    }
    if conv.project:
        meta["project"] = conv.project
    if conv.started:
        meta["started"] = _iso(conv.started)
    if conv.ended:
        meta["ended"] = _iso(conv.ended)
    if conv.model:
        meta["model"] = conv.model
    if conv.turns is not None:
        meta["turns"] = conv.turns
    if conv.tool_counts:
        meta["tool_counts"] = dict(conv.tool_counts)
    if conv.token_totals:
        meta["token_totals"] = dict(conv.token_totals)

    summary = summarizer.summarize(conv) if summarizer is not None else None

    if summary:
        body = f"# {conv.title}\n\n{summary.strip()}\n\n---\n\nFull transcript: [[{bundle_rel}]]\n"
    else:
        body = (
            f"# {conv.title}\n\n"
            f"*Conversation with {conv.agent}"
            + (f" in project {conv.project}" if conv.project else "")
            + ".*\n\n"
            f"Full transcript: [[{bundle_rel}]]\n"
        )

    slug = slugify(f"{conv.agent}-{conv.session_id}")
    page_path = topic_dir / f"{slug}.md"
    page_path.write_text(render_page(meta, body))

    append_log(
        vault_path,
        "sync" if summarizer is None else "sync-summarized",
        f"{conv.agent}:{conv.session_id} -> {topic}/{slug}.md",
    )

    return page_path


# Type stubs so this module doesn't depend on redact/summarize at import time.
class _Redactor:
    def redact(self, text: str) -> str: ...


class _Summarizer:
    def summarize(self, conv: Conversation) -> str | None: ...


# Re-exported names used for type hints in ingest_conversation signature.
Redactor = _Redactor
Summarizer = _Summarizer
