"""VaultService facade: one method per command, returns plain data, never prints.

`LocalVaultService` is the in-process facade used by both the CLI (local vaults)
and the FastAPI server. `RemoteVaultService` (see remote.py) is the HTTP-backed
drop-in the CLI uses for remote vaults. Both honor the same contract.
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from agent_wiki.config import load_vault_config
from agent_wiki.context import run_context
from agent_wiki.conversation import BUNDLE_SUBDIR
from agent_wiki.conversation import ingest_conversation as _ingest_conversation
from agent_wiki.ingest import ingest_file
from agent_wiki.lint import lint_vault
from agent_wiki.log import read_log
from agent_wiki.page import parse_page
from agent_wiki.search import search_vault
from agent_wiki.show import read_vault_bytes, read_vault_file
from agent_wiki.sync import synced_count


def _build_summarizer(vault_config: dict):
    from agent_wiki.summarize import make_summarizer
    return make_summarizer(vault_config.get("summarizer") or {})


def _build_redactor(vault_config: dict):
    from agent_wiki.redact import make_redactor
    return make_redactor(vault_config.get("redaction") or {})


class VaultService(ABC):
    """The command surface shared by the local facade and the remote client."""

    @abstractmethod
    def search(self, query: str, topic: str | None = None,
               limit: int = 20, partial_limit: int = 5) -> dict: ...

    @abstractmethod
    def show(self, rel: str) -> str: ...

    @abstractmethod
    def read_bytes(self, rel: str) -> bytes: ...

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def log(self, last: int | None = None) -> list[str]: ...

    @abstractmethod
    def lint(self) -> list[dict]: ...

    @abstractmethod
    def context(self, prompt: str) -> str: ...

    @abstractmethod
    def ingest(self, source: Path, topic: str | None = None,
               tags: list[str] | None = None) -> dict: ...

    @abstractmethod
    def ingest_conversation(self, bundle: Path, no_summarize: bool = False) -> dict: ...


class LocalVaultService(VaultService):
    def __init__(self, vault_path: Path):
        self.vault_path = vault_path

    # --- reads (no lock) ---
    def search(self, query: str, topic: str | None = None,
               limit: int = 20, partial_limit: int = 5) -> dict:
        results = search_vault(self.vault_path, query, topic=topic)
        all_hits = [r for r in results if r["match_kind"] == "all"]
        partial_hits = [r for r in results if r["match_kind"] == "partial"]
        shown_all = all_hits[:limit]
        shown_partial = partial_hits[:partial_limit]
        total = len(all_hits) + len(partial_hits)
        shown = len(shown_all) + len(shown_partial)
        return {
            "all": shown_all,
            "partial": shown_partial,
            "total": total,
            "shown": shown,
            "truncated": shown < total,
        }

    def show(self, rel: str) -> str:
        return read_vault_file(self.vault_path, rel)

    def read_bytes(self, rel: str) -> bytes:
        return read_vault_bytes(self.vault_path, rel)

    def status(self) -> dict:
        vault = self.vault_path
        config = load_vault_config(vault)
        topics_out = []
        total = 0
        for topic in config.get("topics", []):
            topic_dir = vault / topic
            if not topic_dir.is_dir():
                continue
            count = len(list(topic_dir.rglob("*.md")))
            total += count
            topics_out.append({"topic": topic, "count": count})
        raw_dir = vault / "raw"
        raw = len([p for p in raw_dir.iterdir() if p.is_file()]) if raw_dir.is_dir() else 0
        sessions_dir = vault / BUNDLE_SUBDIR
        bundles = len(list(sessions_dir.glob("*.md"))) if sessions_dir.is_dir() else 0
        recent = read_log(vault, last=1)
        return {
            "vault": str(vault),
            "topics": topics_out,
            "raw": raw,
            "bundles": bundles,
            "sessions_synced": synced_count(vault),
            "total": total,
            "last_activity": recent[0] if recent else None,
        }

    def log(self, last: int | None = None) -> list[str]:
        return read_log(self.vault_path, last=last)

    def lint(self) -> list[dict]:
        return lint_vault(self.vault_path)

    def context(self, prompt: str) -> str:
        return run_context(prompt, self.vault_path) or ""

    # --- writes ---
    def ingest(self, source: Path, topic: str | None = None,
               tags: list[str] | None = None) -> dict:
        page_path = ingest_file(source, self.vault_path, topic=topic, tags=tags)
        meta = parse_page(page_path)["meta"] or {}
        return {
            "page": str(page_path.relative_to(self.vault_path)),
            "title": meta.get("title", page_path.stem),
            "topic": meta.get("topic", topic),
            "sources": meta.get("sources", []),
        }

    def ingest_conversation(self, bundle: Path, no_summarize: bool = False) -> dict:
        config = load_vault_config(self.vault_path)
        src = Path(bundle).resolve()
        sessions_dir = (self.vault_path / BUNDLE_SUBDIR).resolve()
        try:
            src.relative_to(sessions_dir)
            bundle_in_vault = src
        except ValueError:
            sessions_dir.mkdir(parents=True, exist_ok=True)
            bundle_in_vault = sessions_dir / src.name
            shutil.copy2(src, bundle_in_vault)
        summarizer = None if no_summarize else _build_summarizer(config)
        redactor = _build_redactor(config)
        page_path = _ingest_conversation(
            bundle_in_vault, self.vault_path,
            summarizer=summarizer, redactor=redactor,
        )
        return {
            "page": str(page_path.relative_to(self.vault_path)),
            "bundle": bundle_in_vault.name,
        }
