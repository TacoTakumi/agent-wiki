"""Build the `awiki directions` output: self-installing agent instructions.

Pure and vault-free. The canonical block lives in packaged data
(`data/directions.md`); this module wraps it in stable markers and, by
default, prepends a header addressed to the installing agent.
"""

from __future__ import annotations

from importlib import resources

BEGIN_MARKER = "<!-- awiki:begin -->"
END_MARKER = "<!-- awiki:end -->"

HEADER = """\
SET UP THE AGENT WIKI FOR THIS PROJECT
(agent: follow these steps; do not echo them back)

1. Find this project's agent-memory file — CLAUDE.md, AGENTS.md, GEMINI.md, or
   whatever this agent reads at startup. Prefer the project-level file; create
   one if none exists.
2. If a block between the awiki markers below already exists in that file, the
   wiki is already set up — STOP, change nothing.
3. Otherwise append the block below. You SHOULD adapt the prose to this project
   (its name, domain, and the surrounding file's tone) — but keep every
   `awiki ...` command and skill name (awiki-save, awiki-ingest) EXACTLY as
   written; they are literal and real.
4. Keep the awiki markers intact so this command stays idempotent.
"""


def _load_block() -> str:
    """Read the canonical block from packaged data."""
    return (resources.files("agent_wiki") / "data" / "directions.md").read_text(
        encoding="utf-8"
    )


def render_block() -> str:
    """The canonical block wrapped in begin/end markers (no header)."""
    body = _load_block().strip("\n")
    return f"{BEGIN_MARKER}\n{body}\n{END_MARKER}\n"


def render_directions(raw: bool = False) -> str:
    """Full `awiki directions` output.

    raw=True  -> just the marked block (for a human pasting manually).
    raw=False -> agent-directed header followed by the marked block.
    """
    block = render_block()
    if raw:
        return block
    return f"{HEADER}\n{block}"
