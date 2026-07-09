"""Build the `awiki guide` output: self-installing agent instructions.

Pure and vault-free. The canonical block lives in packaged data
(`data/guide.md`); this module emits it verbatim and, by default, prepends a
short header telling the installing agent to add it once. The block is static —
there are no version markers or staleness notes to keep in sync.
"""

from __future__ import annotations

from importlib import resources

HEADER = """\
SET UP THE AGENT WIKI FOR THIS PROJECT
(agent: follow these steps; do not echo them back)

Add the block below, once, to this project's agent-memory file — CLAUDE.md,
AGENTS.md, GEMINI.md, or whatever this agent reads at startup (prefer the
project-level file; create one if none exists). You SHOULD adapt the prose to
this project's name, domain, and the surrounding file's tone — but keep every
`awiki ...` command and skill name (awiki-save, awiki-ingest) exactly as
written; they are literal.

If a "Knowledge base: the Agent Wiki" block is already present in that file,
leave it as-is and change nothing. The block is static — there is nothing to
keep in sync.
"""


def _load_block() -> str:
    """Read the canonical block from packaged data."""
    return (resources.files("agent_wiki") / "data" / "guide.md").read_text(
        encoding="utf-8"
    )


def render_block() -> str:
    """The canonical block, verbatim (packaged data), with a single trailing newline.

    Static: no version markers, no staleness note — nothing to keep in sync.
    """
    return _load_block().strip("\n") + "\n"


def render_guide(raw: bool = False) -> str:
    """Full `awiki guide` output.

    raw=True  -> just the block (for a human pasting manually).
    raw=False -> add-once header followed by the block.
    """
    block = render_block()
    if raw:
        return block
    return f"{HEADER}\n{block}"
