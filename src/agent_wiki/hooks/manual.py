"""Manual backend: prints copy-paste instructions; touches no files."""

from __future__ import annotations

from pathlib import Path


INSTRUCTIONS = """\
To wire agent-wiki into any host that supports a UserPromptSubmit-style hook:

  Command:  awiki context
  Stdin:    JSON with a "prompt" field, e.g. {"prompt": "user text"}
  Stdout:   JSON of shape {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}
            (use `awiki context --output-format plain` for bare text)
  Exit:     Always 0 (silent-fail on every error).

Agent-specific wiring:

  Claude Code:  add to ~/.claude/settings.json under hooks.UserPromptSubmit —
                or run `awiki hook install --agent claude`.
  Other hosts:  consult the host's hook docs and invoke `awiki context`.
"""


def install(config_path: Path | None = None) -> str:
    """Return the instructions string for the caller to print."""
    return INSTRUCTIONS


def uninstall(config_path: Path | None = None) -> str:
    return "Manual backend has nothing to remove."


def status(config_path: Path | None = None) -> str:
    return "Manual backend: no state. Instructions available via `awiki hook install --agent manual`."
