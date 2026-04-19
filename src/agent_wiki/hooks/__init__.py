"""Agent-specific hook install backends."""

from __future__ import annotations

from agent_wiki.hooks import claude, manual


BACKENDS: dict[str, dict] = {
    "claude": {
        "install": claude.install,
        "uninstall": claude.uninstall,
        "status": claude.status,
    },
    "manual": {
        "install": manual.install,
        "uninstall": manual.uninstall,
        "status": manual.status,
    },
}


def get_backend(agent: str) -> dict:
    """Return the backend callable-dict for `agent`. Raises KeyError if unknown."""
    try:
        return BACKENDS[agent]
    except KeyError:
        raise KeyError(
            f"Unknown --agent {agent!r}. "
            f"Supported: {', '.join(sorted(BACKENDS))}. "
            f"Use --agent manual to print wiring instructions for any host."
        )
