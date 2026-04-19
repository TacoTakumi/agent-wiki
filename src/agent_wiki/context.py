"""Auto-context hook: extract keywords from a prompt, search the vault, emit pointers."""

from __future__ import annotations

import os

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
        f"Use awiki-search to read any in full. -->",
    ]
    for topic in topic_order:
        if topic not in by_topic:
            continue
        lines.append(f"## {topic}")
        for hit in by_topic[topic]:
            lines.append(f"- [{hit['title']}]({hit['path']})")
    return "\n".join(lines) + "\n"


def _log_error(msg: str) -> None:
    """Append a diagnostic line to ~/.cache/agent-wiki/context.log.

    Silent on logging failure — we cannot let logging itself break the hook.
    Rotates (rename to .log.old) when the file exceeds ~1MB.
    """
    try:
        cache = Path(
            os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        ) / "agent-wiki"
        cache.mkdir(parents=True, exist_ok=True)
        log = cache / "context.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            rotated = cache / "context.log.old"
            if rotated.exists():
                rotated.unlink()
            log.rename(rotated)
        with log.open("a") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def run_context(prompt: str, vault_path: Path) -> str | None:
    """Top-level orchestration for the auto-context hook.

    Returns the rendered context block, or None when anything short-circuits
    (skip rule, disabled, no hits, any error). Never raises.
    """
    from agent_wiki.config import auto_context_enabled, load_vault_config
    from agent_wiki.search import search_vault

    try:
        if should_skip(prompt):
            return None
        if not auto_context_enabled(vault_path):
            return None

        try:
            vault_config = load_vault_config(vault_path)
        except FileNotFoundError:
            return None
        topics = vault_config.get("topics") or []

        keywords = extract_keywords(prompt)
        if not keywords:
            return None

        # OR-join keywords for ripgrep/Python regex.
        import re
        query = "|".join(re.escape(k) for k in keywords)
        hits = search_vault(vault_path, query)
        if not hits:
            return None

        hits.sort(key=lambda h: (-len(h.get("matches", [])), h["path"]))

        block = build_context_block(hits, topic_order=topics)
        return block or None
    except Exception as exc:  # pragma: no cover — silent-fail net
        _log_error(f"run_context: {type(exc).__name__}: {exc}")
        return None
