"""Claude Code hook backend: manage ~/.claude/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path


AWIKI_COMMAND = "awiki context"
CLAUDE_EVENT = "UserPromptSubmit"

SWEEP_COMMAND = "awiki sync --detach"
SWEEP_EVENT = "SessionStart"

# The two hooks awiki wires into an agent: ``context`` injects wiki hits into
# each prompt; ``sweep`` kicks off a detached session sync whenever an agent
# starts. Keyed by the --only name.
HOOKS: dict[str, dict[str, str]] = {
    "context": {"event": CLAUDE_EVENT, "command": AWIKI_COMMAND},
    "sweep": {"event": SWEEP_EVENT, "command": SWEEP_COMMAND},
}
HOOK_NAMES = tuple(HOOKS)


def select_hooks(only: str | None) -> list[str]:
    """Resolve ``--only`` into the hook names to act on (all when unset)."""
    if only is None:
        return list(HOOK_NAMES)
    if only not in HOOKS:
        raise ValueError(f"Unknown --only {only!r}. Supported: {', '.join(HOOK_NAMES)}.")
    return [only]


def _default_settings_path() -> Path:
    env = os.environ.get("CLAUDE_SETTINGS_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "settings.json"


def _read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text()
    if text.strip() == "":
        return {}
    try:
        return json.loads(text)
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


def _add_hook(data: dict, event: str, command: str, matcher: str | None) -> bool:
    """Ensure one ``command`` entry exists under ``event``. Returns True if added."""
    hooks = data.setdefault("hooks", {})
    groups = hooks.setdefault(event, [])
    for group in groups:
        if any(h.get("command") == command for h in group.get("hooks", [])):
            return False
    # Reuse the first group for the context hook (Claude uses one matcher
    # bucket per event and the existing installs live there); the sweep gets
    # its own matcher-less group so it fires for every SessionStart source
    # regardless of what other groups restrict themselves to.
    if matcher is not None and groups:
        group = groups[0]
        group.setdefault("hooks", [])
    else:
        group = {"hooks": []}
        if matcher is not None:
            group["matcher"] = matcher
        groups.append(group)
    group["hooks"].append({"type": "command", "command": command})
    return True


def install(config_path: Path | None = None, only: str | None = None) -> str:
    """Wire awiki's hooks into Claude Code's settings.

    Installs the ``UserPromptSubmit`` auto-context hook and the ``SessionStart``
    sweep (``awiki sync --detach``); ``only`` narrows to one of them.
    Idempotent: re-running does not duplicate. Preserves all other keys.
    Raises ValueError if the target file is malformed JSON.
    """
    names = select_hooks(only)
    path = config_path or _default_settings_path()
    data = _read_settings(path)

    added: list[str] = []
    for name in names:
        spec = HOOKS[name]
        matcher = "*" if name == "context" else None
        if _add_hook(data, spec["event"], spec["command"], matcher):
            added.append(spec["command"])

    if not added:
        return f"Hook already installed at {path}."
    _atomic_write_json(path, data)
    return "Installed " + ", ".join(f"`{c}`" for c in added) + f" into {path}."


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
