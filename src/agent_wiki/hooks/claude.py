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


def _remove_hook(data: dict, event: str, command: str) -> bool:
    """Drop every ``command`` entry under ``event``; prune emptied groups/keys."""
    hooks = data.get("hooks") or {}
    groups = hooks.get(event) or []
    removed = False
    for group in groups:
        if "hooks" not in group:
            continue  # foreign group shape; leave it exactly as found
        before = len(group["hooks"])
        group["hooks"] = [h for h in group["hooks"] if h.get("command") != command]
        if len(group["hooks"]) != before:
            removed = True
    if event in hooks:
        hooks[event] = [g for g in groups if g.get("hooks") or "hooks" not in g]
        if not hooks[event]:
            del hooks[event]
    if "hooks" in data and not data["hooks"]:
        del data["hooks"]
    return removed


def uninstall(config_path: Path | None = None, only: str | None = None) -> str:
    """Remove awiki's hook entries (both, or the one named by ``only``).

    Idempotent; foreign hooks in the same events are left untouched, and
    emptied groups/event keys are pruned.
    """
    names = select_hooks(only)
    path = config_path or _default_settings_path()
    if not path.exists():
        return f"Nothing to uninstall: {path} does not exist."
    data = _read_settings(path)

    removed: list[str] = []
    for name in names:
        spec = HOOKS[name]
        if _remove_hook(data, spec["event"], spec["command"]):
            removed.append(spec["command"])

    if not removed:
        return "Nothing to uninstall."
    _atomic_write_json(path, data)
    return "Uninstalled " + ", ".join(f"`{c}`" for c in removed) + "."


def _is_installed(data: dict, event: str, command: str) -> bool:
    for group in (data.get("hooks") or {}).get(event, []):
        if any(h.get("command") == command for h in group.get("hooks", [])):
            return True
    return False


def status(config_path: Path | None = None) -> str:
    """Report, per hook, whether it is wired into the target settings."""
    path = config_path or _default_settings_path()
    if not path.exists():
        data: dict = {}
        where = f"{path} does not exist"
    else:
        try:
            data = _read_settings(path)
        except ValueError:
            return f"Cannot read {path}: malformed JSON."
        where = str(path)
    lines = [f"Claude Code hooks ({where}):"]
    for name, spec in HOOKS.items():
        state = "installed" if _is_installed(data, spec["event"], spec["command"]) else "not installed"
        lines.append(f"  {name:<8} {spec['event']} `{spec['command']}`: {state}")
    return "\n".join(lines)
