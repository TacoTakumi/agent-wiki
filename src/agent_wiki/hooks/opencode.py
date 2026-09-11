"""OpenCode hook backend: manage an awiki-owned plugin in OpenCode's global plugin dir.

OpenCode loads every JavaScript/TypeScript file in ``~/.config/opencode/plugins/``
(``$XDG_CONFIG_HOME/opencode/plugins/`` when XDG is set). A plugin exports an
async factory receiving ``{ project, client, $, directory, worktree }`` and
returning hooks; the ``event`` hook sees every bus event, and ``session.created``
fires when a session starts. Commands run through Bun's ``$`` shell, the
mechanism the plugin docs prescribe (verified against opencode 1.15.6).

The plugin runs ``awiki sync --detach`` with ``.nothrow().quiet()`` inside a
try/catch so a missing or failing awiki never affects OpenCode. The file carries
a marker line so uninstall only ever deletes what awiki wrote. OpenCode has no
prompt-level hook equivalent to Claude Code's auto-context, so the ``context``
hook is a documented no-op here.
"""

from __future__ import annotations

import os
from pathlib import Path

MARKER = "awiki-managed plugin"
PLUGIN_NAME = "awiki-sync.js"
SWEEP_COMMAND = "awiki sync --detach"

PLUGIN_SOURCE = f"""\
// {MARKER}: startup session sweep.
// Written by `awiki hook install --agent opencode`; remove with `awiki hook uninstall --agent opencode`.
// Do not edit by hand - a reinstall overwrites this file.

export const AwikiSyncPlugin = async ({{ $ }}) => {{
  return {{
    event: async ({{ event }}) => {{
      if (event.type !== "session.created") return;
      try {{
        // `{SWEEP_COMMAND}` returns at once; the sync itself runs in the
        // background and logs to awiki's state dir.
        await $`awiki sync --detach`.nothrow().quiet();
      }} catch {{
        // awiki missing, slow, or failing must never affect OpenCode.
      }}
    }},
  }};
}};
"""


def _default_plugin_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return config_home / "opencode" / "plugins" / PLUGIN_NAME


def _is_managed(path: Path) -> bool:
    try:
        return MARKER in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


_CONTEXT_NOOP = (
    "OpenCode has no auto-context hook (no prompt-submit event to attach `awiki context` to); "
    "nothing to do for --only context. The startup sweep is the OpenCode hook: omit --only "
    "or pass --only sweep."
)


def install(config_path: Path | None = None, only: str | None = None) -> str:
    """Write the sweep plugin. Idempotent; a foreign file at the path is refused."""
    if only == "context":
        return _CONTEXT_NOOP
    path = config_path or _default_plugin_path()
    if path.exists():
        if not _is_managed(path):
            raise ValueError(
                f"Refusing to overwrite {path}: it is not awiki-managed (no marker). "
                "Move it aside or pass --config-path."
            )
        if path.read_text(encoding="utf-8") == PLUGIN_SOURCE:
            return f"Hook already installed at {path}."
    _atomic_write(path, PLUGIN_SOURCE)
    return f"Installed `{SWEEP_COMMAND}` on session.created into {path}."


def uninstall(config_path: Path | None = None, only: str | None = None) -> str:
    """Delete the sweep plugin, but only if it carries the awiki marker."""
    if only == "context":
        return "OpenCode has no auto-context hook; nothing to remove for --only context."
    path = config_path or _default_plugin_path()
    if not path.exists():
        return f"Nothing to uninstall: {path} does not exist."
    if not _is_managed(path):
        return f"Left {path} in place: it is not awiki-managed (no marker)."
    path.unlink()
    return f"Uninstalled {path}."


def status(config_path: Path | None = None) -> str:
    path = config_path or _default_plugin_path()
    line = f"  sweep    session.created `{SWEEP_COMMAND}`: "
    if path.exists() and _is_managed(path):
        return f"OpenCode hooks ({path}):\n{line}installed"
    if path.exists():
        return f"OpenCode hooks ({path}):\n{line}not installed (a file exists there but is not awiki-managed)"
    return f"OpenCode hooks ({path}):\n{line}not installed"
