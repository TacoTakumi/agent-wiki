"""Auto-context hook: extract keywords from a prompt, search the vault, emit pointers."""

from __future__ import annotations

import json
import os
import time

from datetime import datetime, timezone
from pathlib import Path


def should_skip(prompt: str) -> bool:
    """Return True if the prompt is not worth searching the wiki for.

    Skip rules:
      - Empty / whitespace-only.
      - Starts with '/' (slash command).
      - Fewer than 3 whitespace-split words.
      - Shorter than 15 characters (after strip).
    """
    stripped = prompt.strip()
    if not stripped:
        return True
    if stripped.startswith("/"):
        return True
    if len(stripped) < 15:
        return True
    if len(stripped.split()) < 3:
        return True
    return False


def extract_keywords(prompt: str, max_keywords: int = 5) -> list[str]:
    """Extract up to `max_keywords` keyphrases from `prompt` via YAKE.

    Returns [] for empty, whitespace-only, or trivially short inputs, and
    for any YAKE failure. Never raises.
    """
    stripped = prompt.strip()
    if len(stripped) < 4:
        return []

    try:
        import yake
        extractor = yake.KeywordExtractor(
            lan="en",
            n=3,  # up to 3-word keyphrases
            top=max_keywords,
            dedupLim=0.9,
        )
        # YAKE returns [(keyword, score), ...] with LOWER score = more relevant.
        results = extractor.extract_keywords(stripped)
    except Exception:
        return []

    return [kw for kw, _score in results]


def build_context_block(
    hits: list[dict],
    topic_order: list[str],
    limit: int = 5,
) -> str:
    """Render a compact pointer block grouped by topic.

    Hits with paths outside any listed topic are dropped (we don't emit
    references to `raw/`, `index.md`, or other orphan files).
    """
    capped = hits[:limit]
    if not capped:
        return ""

    # Group by leading path segment; keep only known topics.
    by_topic: dict[str, list[dict]] = {}
    for hit in capped:
        parts = hit["path"].split("/", 1)
        if len(parts) != 2:
            continue
        topic = parts[0]
        if topic not in topic_order:
            continue
        by_topic.setdefault(topic, []).append(hit)

    if not by_topic:
        return ""

    total = sum(len(v) for v in by_topic.values())
    lines = [
        f"<!-- agent-wiki: {total} possibly-relevant "
        f"{'page' if total == 1 else 'pages'}. "
        f"Use `awiki show <path>` to read any in full. -->",
    ]
    for topic in topic_order:
        if topic not in by_topic:
            continue
        lines.append(f"## {topic}")
        for hit in by_topic[topic]:
            lines.append(f"- [{hit['title']}]({hit['path']})")
    return "\n".join(lines) + "\n"


def _cache_dir() -> Path:
    return Path(
        os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    ) / "agent-wiki"


def _append_rotated(filename: str, line: str) -> None:
    """Append `line` to ~/.cache/agent-wiki/<filename> with 1MB rotation.

    Silent on logging failure — we cannot let logging itself break the hook.
    """
    try:
        cache = _cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        log = cache / filename
        if log.exists() and log.stat().st_size > 1_000_000:
            rotated = cache / (filename + ".old")
            if rotated.exists():
                rotated.unlink()
            log.rename(rotated)
        with log.open("a") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass


def _log_error(msg: str) -> None:
    """Append a diagnostic line to ~/.cache/agent-wiki/context.log."""
    _append_rotated("context.log", msg)


def _debug_enabled() -> bool:
    val = os.environ.get("AWIKI_CONTEXT_DEBUG")
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _log_debug(trace: dict) -> None:
    """Append a JSONL trace to ~/.cache/agent-wiki/context.debug.log when enabled."""
    if not _debug_enabled():
        return
    try:
        line = json.dumps(trace, ensure_ascii=False)
    except Exception:
        return
    _append_rotated("context.debug.log", line)


def run_context(prompt: str, vault_path: Path) -> str | None:
    """Top-level orchestration for the auto-context hook.

    Returns the rendered context block, or None when anything short-circuits
    (skip rule, disabled, no hits, any error). Never raises.
    """
    from agent_wiki.config import auto_context_enabled, load_vault_config
    from agent_wiki.search import search_vault

    t0 = time.monotonic()
    trace: dict = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "prompt": prompt[:200],
        "prompt_len": len(prompt),
        "outcome": None,
        "keywords": [],
        "hits_raw": 0,
        "hits_rendered": 0,
        "block_chars": 0,
        "block": "",
    }

    def finish(block: str | None) -> str | None:
        trace["duration_ms"] = int((time.monotonic() - t0) * 1000)
        _log_debug(trace)
        return block

    try:
        if should_skip(prompt):
            trace["outcome"] = "skip_rule"
            return finish(None)
        if not auto_context_enabled(vault_path):
            trace["outcome"] = "disabled"
            return finish(None)

        try:
            vault_config = load_vault_config(vault_path)
        except FileNotFoundError:
            trace["outcome"] = "no_vault_config"
            return finish(None)
        topics = vault_config.get("topics") or []

        keywords = extract_keywords(prompt)
        trace["keywords"] = keywords
        if not keywords:
            trace["outcome"] = "no_keywords"
            return finish(None)

        # Space-join keywords; search_vault tokenizes (AND/partial + coverage).
        query = " ".join(keywords)
        hits = search_vault(vault_path, query)
        trace["hits_raw"] = len(hits)
        if not hits:
            trace["outcome"] = "no_hits"
            return finish(None)

        hits.sort(key=lambda h: (-h.get("coverage", 0), -len(h.get("matches", [])), h["path"]))

        block = build_context_block(hits, topic_order=topics)
        if not block:
            trace["outcome"] = "all_filtered"
            return finish(None)

        trace["hits_rendered"] = block.count("\n- [")
        trace["block_chars"] = len(block)
        trace["block"] = block
        trace["outcome"] = "ok"
        return finish(block)
    except Exception as exc:  # pragma: no cover — silent-fail net
        _log_error(f"run_context: {type(exc).__name__}: {exc}")
        trace["outcome"] = "error"
        trace["error"] = f"{type(exc).__name__}: {exc}"
        return finish(None)
