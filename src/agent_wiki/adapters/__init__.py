"""Conversation adapters: agent-native formats → canonical Conversation bundles.

Each adapter is instantiated with a config dict (from wiki.yaml `sources.<name>`)
and exposes:

- ``discover()`` → iterable of opaque ``SessionRef`` values (anything the
  adapter understands internally — usually a ``Path`` or string session id).
- ``session_key(ref)`` → ``"<agent>:<session_id>"``, the sync state key,
  computed cheaply (filename, row id, or frontmatter header only) so the
  sync loop can consult state without parsing the transcript.
- ``fingerprint(ref)`` → string used for idempotent sync state tracking.
  Typically ``"<mtime>"`` or ``"<hash>"``.
- ``to_bundle(ref)`` → ``Conversation``.

The registry at the bottom of this module is how ``sync.py`` finds adapters
by name. Adding a new adapter = writing a class + one line in the registry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from agent_wiki.conversation import Conversation


class ConversationAdapter(ABC):
    """Abstract base for all conversation sources."""

    name: str = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    def discover(self) -> Iterable[Any]:
        """Yield session references (adapter-internal type)."""

    @abstractmethod
    def session_key(self, ref: Any) -> str:
        """Return ``"<agent>:<session_id>"`` without parsing the transcript body.

        Must equal ``f"{conv.agent}:{conv.session_id}"`` of the Conversation
        that ``to_bundle(ref)`` would produce.
        """

    @abstractmethod
    def fingerprint(self, ref: Any) -> str:
        """Return a stable string reflecting current session state."""

    @abstractmethod
    def to_bundle(self, ref: Any) -> Conversation:
        """Convert a session reference into a Conversation."""


def build_adapter(name: str, config: dict[str, Any] | None = None) -> ConversationAdapter:
    """Factory: name → adapter instance.

    Raises KeyError for unknown names. Imports are deferred so adapters don't
    all load unless asked for.
    """
    if name in ("claude-code", "claude_code", "cc"):
        from agent_wiki.adapters.claude_code import ClaudeCodeAdapter
        return ClaudeCodeAdapter(config)
    if name == "opencode":
        from agent_wiki.adapters.opencode import OpencodeAdapter
        return OpencodeAdapter(config)
    if name == "pi":
        from agent_wiki.adapters.pi import PiAdapter
        return PiAdapter(config)
    if name in ("drop-zone", "drop_zone", "dropzone"):
        from agent_wiki.adapters.drop_zone import DropZoneAdapter
        return DropZoneAdapter(config)
    raise KeyError(f"unknown adapter: {name!r}")


ADAPTER_NAMES = ("claude-code", "opencode", "pi", "drop-zone")

# Adapters whose session references are filesystem paths (the CLI's ``adapt``
# command turns the ref argument into a Path for these).
PATH_REF_ADAPTERS = ("claude-code", "pi")
