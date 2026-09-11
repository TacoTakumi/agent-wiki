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
    "pi has no auto-context hook (no prompt-submit event to attach `awiki context` to); "
    "nothing to do for --only context. The startup sweep is the pi hook: omit --only "
    "or pass --only sweep."
)


def install(config_path: Path | None = None, only: str | None = None) -> str:
    """Write the sweep extension. Idempotent; a foreign file at the path is refused."""
    if only == "context":
        return _CONTEXT_NOOP
    path = config_path or _default_extension_path()
    if path.exists():
        if not _is_managed(path):
            raise ValueError(
                f"Refusing to overwrite {path}: it is not awiki-managed (no marker). "
                "Move it aside or pass --config-path."
            )
        if path.read_text(encoding="utf-8") == EXTENSION_SOURCE:
            return f"Hook already installed at {path}."
    _atomic_write(path, EXTENSION_SOURCE)
    return f"Installed `{SWEEP_COMMAND}` on session_start into {path}."


def uninstall(config_path: Path | None = None, only: str | None = None) -> str:
    """Delete the sweep extension, but only if it carries the awiki marker."""
    if only == "context":
        return "pi has no auto-context hook; nothing to remove for --only context."
    path = config_path or _default_extension_path()
    if not path.exists():
        return f"Nothing to uninstall: {path} does not exist."
    if not _is_managed(path):
        return f"Left {path} in place: it is not awiki-managed (no marker)."
    path.unlink()
    return f"Uninstalled {path}."


def status(config_path: Path | None = None) -> str:
    path = config_path or _default_extension_path()
    if path.exists() and _is_managed(path):
        return f"pi hooks ({path}):\n  sweep    session_start `{SWEEP_COMMAND}`: installed"
    if path.exists():
        return (f"pi hooks ({path}):\n  sweep    session_start `{SWEEP_COMMAND}`: "
                "not installed (a file exists there but is not awiki-managed)")
    return f"pi hooks ({path}):\n  sweep    session_start `{SWEEP_COMMAND}`: not installed"
