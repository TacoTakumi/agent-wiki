"""Manual backend: prints copy-paste instructions; touches no files."""

from __future__ import annotations

from pathlib import Path


INSTRUCTIONS = """\
awiki wires two hooks into an agent. Either can be installed by hand:

1. Auto-context (every prompt)

  Command:  awiki context
  Stdin:    JSON with a "prompt" field, e.g. {"prompt": "user text"}
  Stdout:   JSON of shape {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}
            (use `awiki context --output-format plain` for bare text)
  Exit:     Always 0 (silent-fail on every error).

  Claude Code:  add to ~/.claude/settings.json under hooks.UserPromptSubmit -
                or run `awiki hook install --agent claude --only context`.
  pi / OpenCode: no prompt-submit hook exists; skip this one.
  Other hosts:  consult the host's hook docs and invoke `awiki context`.

2. Startup session sweep (every agent start)

  Command:  awiki sync --detach
            Returns at once; the sync runs in the background and logs to the
            awiki state dir. Safe to run from several agents at once: a sweep
            that finds another sync holding the vault lock exits cleanly.
  Exit:     Always 0.

  Claude Code:  add to ~/.claude/settings.json under hooks.SessionStart with no
                matcher (fires for startup, resume, clear, compact, fork) -
                or run `awiki hook install --agent claude`.
  pi:           an extension in ~/.pi/agent/extensions/ that handles session_start
                and calls pi.exec("awiki", ["sync", "--detach"], { timeout: 5000 })
                inside try/catch - or run `awiki hook install --agent pi`.
  OpenCode:     a plugin in ~/.config/opencode/plugins/ whose event hook runs
                $`awiki sync --detach`.nothrow().quiet() on session.created
                inside try/catch - or run `awiki hook install --agent opencode`.
  Other hosts:  run `awiki sync --detach` from whatever startup hook the host offers.
"""


def install(config_path: Path | None = None, only: str | None = None) -> str:
    """Return the instructions string for the caller to print."""
    return INSTRUCTIONS


def uninstall(config_path: Path | None = None, only: str | None = None) -> str:
    return "Manual backend has nothing to remove."


def status(config_path: Path | None = None) -> str:
    return "Manual backend: no state. Instructions available via `awiki hook install --agent manual`."
