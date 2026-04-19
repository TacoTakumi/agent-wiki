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
