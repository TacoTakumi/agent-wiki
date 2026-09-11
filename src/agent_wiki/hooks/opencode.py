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

from agent_wiki.hooks.managed_file import ManagedFileHook

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


_HOOK = ManagedFileHook(
    host="OpenCode", event="session.created", command=SWEEP_COMMAND, marker=MARKER,
    source=PLUGIN_SOURCE, default_path=_default_plugin_path,
)

install = _HOOK.install
uninstall = _HOOK.uninstall
status = _HOOK.status
