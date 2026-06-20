"""Build the `awiki directions` output: self-installing agent instructions.

Pure and vault-free. The canonical block lives in packaged data
(`data/directions.md`); this module wraps it in stable markers (the begin
marker carries the package version) and, by default, prepends a header
addressed to the installing agent.
"""

from __future__ import annotations

from importlib import resources

from agent_wiki import __version__

# The begin marker carries the version (e.g. `<!-- awiki:begin v0.1.0 -->`).
# BEGIN_PREFIX is the version-independent anchor an agent greps for; END_MARKER
# is constant.
BEGIN_PREFIX = "<!-- awiki:begin"
END_MARKER = "<!-- awiki:end -->"

HEADER = """\
SET UP THE AGENT WIKI FOR THIS PROJECT
(agent: follow these steps; do not echo them back)

1. Find this project's agent-memory file — CLAUDE.md, AGENTS.md, GEMINI.md, or
   whatever this agent reads at startup. Prefer the project-level file; create
   one if none exists.
2. If a block between the awiki markers below already exists in that file, read
   its version from the `<!-- awiki:begin vX.Y.Z -->` marker. If that version is
   the same as (or newer than) the block below, the wiki is already set up —
   STOP, change nothing.
3. If the existing block's version is OLDER than the block below, replace the old
   block — everything from its `<!-- awiki:begin ... -->` through
   `<!-- awiki:end -->` — with the block below. This is a version upgrade.
4. If no block exists yet, append the block below. You SHOULD adapt the prose to
   this project (its name, domain, and the surrounding file's tone) — but keep
   every `awiki ...` command, skill name (awiki-save, awiki-ingest), and the
   awiki markers (including the version) EXACTLY as written; they are literal.
5. Keep the awiki markers intact so this command stays idempotent.
"""

# Visible note appended inside the block so the version travels into the agent's
# memory file and the agent knows how to detect/refresh a stale copy.
VERSION_NOTE = """\
*These directions are awiki v{version}. If `awiki --version` ever reports a newer \
version than the one in this block's `awiki:begin` marker, re-run \
`awiki directions` and re-sync this block.*"""


def begin_marker(version: str = __version__) -> str:
    """The version-stamped begin marker, e.g. `<!-- awiki:begin v0.1.0 -->`."""
    return f"{BEGIN_PREFIX} v{version} -->"


def _load_block() -> str:
    """Read the canonical block from packaged data."""
    return (resources.files("agent_wiki") / "data" / "directions.md").read_text(
        encoding="utf-8"
    )


def render_block(version: str = __version__) -> str:
    """The canonical block wrapped in version-stamped begin/end markers.

    Includes the version note so the embedded version lands in the agent's
    memory file (no header).
    """
    body = _load_block().strip("\n")
    note = VERSION_NOTE.format(version=version)
    return f"{begin_marker(version)}\n{body}\n\n{note}\n{END_MARKER}\n"


def render_directions(raw: bool = False) -> str:
    """Full `awiki directions` output.

    raw=True  -> just the marked block (for a human pasting manually).
    raw=False -> agent-directed header followed by the marked block.
    """
    block = render_block()
    if raw:
        return block
    return f"{HEADER}\n{block}"
