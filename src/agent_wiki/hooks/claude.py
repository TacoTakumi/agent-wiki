"""Claude Code hook backend: manage ~/.claude/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path


AWIKI_COMMAND = "awiki context"
CLAUDE_EVENT = "UserPromptSubmit"


def _default_settings_path() -> Path:
    env = os.environ.get("CLAUDE_SETTINGS_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "settings.json"


def _read_settings(path: Path) -> dict:
    if not path.exists() or path.read_text().strip() == "":
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Refusing to modify {path}: file is not valid JSON ({exc}). "
            "Fix or remove it and retry."
        )


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def install(config_path: Path | None = None) -> str:
    """Add a UserPromptSubmit hook entry for `awiki context`.

    Idempotent: re-running does not duplicate. Preserves all other keys.
    Raises ValueError if the target file is malformed JSON.
    """
    path = config_path or _default_settings_path()
    data = _read_settings(path)

    hooks = data.setdefault("hooks", {})
    events = hooks.setdefault(CLAUDE_EVENT, [])

    # Find (or create) a matcher group; Claude uses one matcher bucket per event.
    if not events:
        events.append({"matcher": "*", "hooks": []})
    group = events[0]
    group.setdefault("hooks", [])

    # De-duplicate by command string.
    if any(h.get("command") == AWIKI_COMMAND for h in group["hooks"]):
        _atomic_write_json(path, data)
        return f"Hook already installed at {path}."

    group["hooks"].append({"type": "command", "command": AWIKI_COMMAND})
    _atomic_write_json(path, data)
    return f"Installed `{AWIKI_COMMAND}` into {path}."


def uninstall(config_path: Path | None = None) -> str:
    """Remove the `awiki context` hook entry. Idempotent."""
    path = config_path or _default_settings_path()
    if not path.exists():
        return f"Nothing to uninstall: {path} does not exist."
    data = _read_settings(path)

    events = data.get("hooks", {}).get(CLAUDE_EVENT, [])
    removed = False
    for group in events:
        before = len(group.get("hooks", []))
        group["hooks"] = [
            h for h in group.get("hooks", [])
            if h.get("command") != AWIKI_COMMAND
        ]
        if len(group["hooks"]) != before:
            removed = True

    # Drop empty groups, then drop the event key entirely if no groups left.
    if events:
        data["hooks"][CLAUDE_EVENT] = [g for g in events if g.get("hooks")]
        if not data["hooks"][CLAUDE_EVENT]:
            del data["hooks"][CLAUDE_EVENT]
        if not data["hooks"]:
            del data["hooks"]

    _atomic_write_json(path, data)
    return "Uninstalled." if removed else "Nothing to uninstall."


def status(config_path: Path | None = None) -> str:
    """Report whether `awiki context` is wired into the target settings."""
    path = config_path or _default_settings_path()
    if not path.exists():
        return f"Not installed ({path} does not exist)."
    try:
        data = _read_settings(path)
    except ValueError:
        return f"Cannot read {path}: malformed JSON."
    events = data.get("hooks", {}).get(CLAUDE_EVENT, [])
    for group in events:
        for h in group.get("hooks", []):
            if h.get("command") == AWIKI_COMMAND:
                return f"Installed at {path}."
    return f"Not installed in {path}."
