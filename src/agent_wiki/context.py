"""Auto-context hook: extract keywords from a prompt, search the vault, emit pointers."""

from __future__ import annotations

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
