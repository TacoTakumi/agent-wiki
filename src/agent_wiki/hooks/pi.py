"""pi hook backend: manage an awiki-owned extension in pi's global extension dir.

pi auto-loads ``<agent-dir>/extensions/*.ts`` (agent dir defaults to
``~/.pi/agent``, overridable with pi's own ``PI_CODING_AGENT_DIR``). The
extension subscribes to ``session_start`` and runs ``awiki sync --detach``
through ``pi.exec`` with a short timeout, swallowing every failure so pi
startup is never affected. The file carries a marker line so uninstall only
ever deletes what awiki wrote.

pi has no prompt-level hook equivalent to Claude Code's auto-context, so the
``context`` hook is a documented no-op here.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_wiki.hooks.managed_file import ManagedFileHook

MARKER = "awiki-managed extension"
EXTENSION_NAME = "awiki-sync.ts"
SWEEP_COMMAND = "awiki sync --detach"

EXTENSION_SOURCE = f"""\
// {MARKER}: startup session sweep.
// Written by `awiki hook install --agent pi`; remove with `awiki hook uninstall --agent pi`.
// Do not edit by hand - a reinstall overwrites this file.
import type {{ ExtensionAPI }} from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {{
  pi.on("session_start", async () => {{
    try {{
      // `{SWEEP_COMMAND}` returns at once; the sync itself runs in the
      // background and logs to awiki's state dir.
      await pi.exec("awiki", ["sync", "--detach"], {{ timeout: 5000 }});
    }} catch {{
      // awiki missing, slow, or failing must never affect pi startup.
    }}
  }});
}}
"""


def _default_extension_path() -> Path:
    env = os.environ.get("PI_CODING_AGENT_DIR")
    agent_dir = Path(env).expanduser() if env else Path.home() / ".pi" / "agent"
    return agent_dir / "extensions" / EXTENSION_NAME


_HOOK = ManagedFileHook(
    host="pi", event="session_start", command=SWEEP_COMMAND, marker=MARKER,
    source=EXTENSION_SOURCE, default_path=_default_extension_path,
)

install = _HOOK.install
uninstall = _HOOK.uninstall
status = _HOOK.status
