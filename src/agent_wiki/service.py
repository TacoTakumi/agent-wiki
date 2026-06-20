"""VaultService facade: one method per command, returns plain data, never prints.

`LocalVaultService` is the in-process facade used by both the CLI (local vaults)
and the FastAPI server. `RemoteVaultService` (see remote.py) is the HTTP-backed
drop-in the CLI uses for remote vaults. Both honor the same contract.
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from agent_wiki.adapters import build_adapter
from agent_wiki.config import load_vault_config
from agent_wiki.context import run_context
from agent_wiki.conversation import BUNDLE_SUBDIR, write_bundle
from agent_wiki.conversation import ingest_conversation as _ingest_conversation
from agent_wiki.doctor import RawContentDrift, SourcePathMissing, run_checks
from agent_wiki.index import rebuild_index as _rebuild_index
from agent_wiki.ingest import ingest_file
from agent_wiki.lint import lint_vault
from agent_wiki.locking import file_lock
from agent_wiki.log import read_log
from agent_wiki.page import parse_page, render_page
from agent_wiki.search import search_vault
from agent_wiki.show import read_vault_bytes, read_vault_file
from agent_wiki.sync import sync as _sync
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
               tags: list[str] | None = None, update: bool = False,
               force: bool = False) -> dict: ...

    @abstractmethod
    def ingest_conversation(self, bundle: Path, no_summarize: bool = False) -> dict: ...

    @abstractmethod
    def rebuild_index(self) -> dict: ...

    @abstractmethod
    def sync(self, source: str | None = None, since: str | None = None,
             dry_run: bool = False, include_live: bool = False) -> dict: ...

    @abstractmethod
    def adapt(self, source: str, ref: str, output: str | None = None) -> dict: ...

    @abstractmethod
    def doctor(self, fix: bool = False, dry_run: bool = False,
               reconcile_raw: bool = False) -> dict: ...


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
               tags: list[str] | None = None, update: bool = False,
               force: bool = False) -> dict:
        with file_lock(self.vault_path, "log"):
            page_path = ingest_file(source, self.vault_path, topic=topic,
                                    tags=tags, update=update, force=force)
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
        with file_lock(self.vault_path, "log"):
            page_path = _ingest_conversation(
                bundle_in_vault, self.vault_path,
                summarizer=summarizer, redactor=redactor,
            )
        return {
            "page": str(page_path.relative_to(self.vault_path)),
            "bundle": bundle_in_vault.name,
        }

    def rebuild_index(self) -> dict:
        with file_lock(self.vault_path, "index"):
            _rebuild_index(self.vault_path)
        return {"ok": True}

    def sync(self, source: str | None = None, since: str | None = None,
             dry_run: bool = False, include_live: bool = False) -> dict:
        config = load_vault_config(self.vault_path)
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                raise ValueError(f"since must be an ISO 8601 date (YYYY-MM-DD): {since}")
        if include_live:
            sources_cfg = config.setdefault("sources", {})
            for name in ("claude_code", "opencode", "drop_zone"):
                if name in sources_cfg:
                    sources_cfg[name] = dict(sources_cfg[name])
                    sources_cfg[name]["include_live"] = True
        summarizer = _build_summarizer(config) if not dry_run else None
        redactor = _build_redactor(config) if not dry_run else None
        if dry_run:
            results = _sync(self.vault_path, source=source, dry_run=True, since=since_dt)
        else:
            with file_lock(self.vault_path, "log"), file_lock(self.vault_path, "index"):
                results = _sync(self.vault_path, source=source, dry_run=False,
                                since=since_dt, summarizer=summarizer, redactor=redactor)
        counts = {"new": 0, "updated": 0, "skipped": 0, "error": 0}
        out = []
        for r in results:
            counts[r.action] = counts.get(r.action, 0) + 1
            out.append({
                "action": r.action,
                "source": r.source,
                "key": r.key,
                "error": r.error,
                "page": str(r.page.relative_to(self.vault_path)) if r.page else None,
            })
        return {"results": out, "counts": counts}

    def adapt(self, source: str, ref: str, output: str | None = None) -> dict:
        config = load_vault_config(self.vault_path)
        cfg = (config.get("sources") or {}).get(source.replace("-", "_"), {})
        adapter = build_adapter(source, cfg)
        ref_value = Path(ref) if source == "claude-code" else ref
        conv = adapter.to_bundle(ref_value)
        if output:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(render_page(conv.frontmatter(), conv.body))
            return {"bundle": str(out_path)}
        path = write_bundle(conv, self.vault_path)
        return {"bundle": str(path.relative_to(self.vault_path))}

    def doctor(self, fix: bool = False, dry_run: bool = False,
               reconcile_raw: bool = False) -> dict:
        findings = run_checks(self.vault_path)
        out_findings = [
            {"name": f.check.name, "detail": f.detail,
             "description": f.check.description}
            for f in findings
        ]
        applied = 0
        skipped = 0
        if (fix or reconcile_raw) and not dry_run:
            with file_lock(self.vault_path, "log"):
                for f in findings:
                    if isinstance(f.check, SourcePathMissing):  # informational only
                        skipped += 1
                        continue
                    if isinstance(f.check, RawContentDrift):
                        if not reconcile_raw:                   # never via blanket --fix
                            skipped += 1
                            continue
                    elif not fix:                               # schema fix needs --fix
                        skipped += 1
                        continue
                    try:
                        f.check.fix(self.vault_path)
                        applied += 1
                    except Exception:
                        skipped += 1
        else:
            skipped = len(findings)
        return {"findings": out_findings, "applied": applied, "skipped": skipped}
