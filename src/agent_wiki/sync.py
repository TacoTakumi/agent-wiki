"""Sync: discover sessions from configured sources and ingest them.

State is tracked in ``<vault>/.awiki-sync-state.json`` so reruns are
idempotent. State entries are keyed by ``"<agent>:<session_id>"`` and hold
the adapter fingerprint plus the bundle/page paths produced.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from agent_wiki.adapters import ADAPTER_NAMES, ConversationAdapter, build_adapter
from agent_wiki.config import load_vault_config
from agent_wiki.conversation import (
    Conversation,
    ingest_conversation,
    write_bundle,
)
from agent_wiki.index import rebuild_index

STATE_FILE = ".awiki-sync-state.json"


@dataclass
class SyncResult:
    source: str
    key: str            # "<agent>:<session_id>"
    action: str         # "new" | "updated" | "skipped" | "error"
    bundle: Path | None = None
    page: Path | None = None
    error: str | None = None


def _state_path(vault_path: Path) -> Path:
    return vault_path / STATE_FILE


def load_state(vault_path: Path) -> dict[str, dict[str, Any]]:
    p = _state_path(vault_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def save_state(vault_path: Path, state: dict[str, dict[str, Any]]) -> None:
    _state_path(vault_path).write_text(json.dumps(state, indent=2, sort_keys=True))


def enabled_sources(vault_path: Path, filter_name: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Return list of (adapter_name, config) for enabled sources.

    ``filter_name`` (optional) restricts to one source and forces it enabled.
    """
    vault_config = load_vault_config(vault_path)
    sources_cfg = vault_config.get("sources") or {}

    if filter_name:
        cfg = sources_cfg.get(filter_name.replace("-", "_"), {})
        return [(filter_name, cfg)]

    out: list[tuple[str, dict[str, Any]]] = []
    for canon in ADAPTER_NAMES:
        key = canon.replace("-", "_")
        cfg = sources_cfg.get(key) or {}
        if cfg.get("enabled"):
            out.append((canon, cfg))
    return out


def sync(
    vault_path: Path,
    source: str | None = None,
    dry_run: bool = False,
    since: datetime | None = None,
    summarizer: Any | None = None,
    redactor: Any | None = None,
) -> list[SyncResult]:
    """Run adapters and ingest new/changed sessions. Returns per-session results."""
    state = load_state(vault_path)
    results: list[SyncResult] = []

    for name, cfg in enabled_sources(vault_path, source):
        try:
            adapter = build_adapter(name, cfg)
        except KeyError as e:
            results.append(SyncResult(source=name, key="", action="error", error=str(e)))
            continue

        if since is not None and hasattr(adapter, "since"):
            adapter.since = since
        if hasattr(adapter, "set_vault"):
            adapter.set_vault(vault_path)

        for ref in adapter.discover():
            try:
                fp = adapter.fingerprint(ref)
            except Exception as e:  # adapter-specific failures (file gone, etc.)
                results.append(SyncResult(source=name, key=str(ref), action="error", error=str(e)))
                continue

            try:
                conv = adapter.to_bundle(ref)
            except Exception as e:
                results.append(SyncResult(source=name, key=str(ref), action="error", error=str(e)))
                continue

            key = f"{conv.agent}:{conv.session_id}"
            prev = state.get(key)
            if prev and prev.get("fingerprint") == fp:
                results.append(SyncResult(source=name, key=key, action="skipped"))
                continue

            if dry_run:
                action = "updated" if prev else "new"
                results.append(SyncResult(source=name, key=key, action=action))
                continue

            try:
                # Drop-zone's to_bundle moves the file into raw/sessions/ itself;
                # for everything else we write the bundle now.
                if name in ("drop-zone", "drop_zone", "dropzone"):
                    bundle_path = vault_path / "raw" / "sessions" / f"{conv.bundle_id()}.md"
                    if not bundle_path.exists():
                        bundle_path = write_bundle(conv, vault_path)
                else:
                    bundle_path = write_bundle(conv, vault_path)
                page_path = ingest_conversation(
                    bundle_path, vault_path,
                    summarizer=summarizer, redactor=redactor,
                )
            except Exception as e:
                results.append(SyncResult(source=name, key=key, action="error", error=str(e)))
                continue

            state[key] = {
                "fingerprint": fp,
                "bundle": str(bundle_path.relative_to(vault_path)),
                "page": str(page_path.relative_to(vault_path)),
                "last_sync": datetime.now().isoformat(timespec="seconds"),
            }
            results.append(SyncResult(
                source=name, key=key,
                action="updated" if prev else "new",
                bundle=bundle_path, page=page_path,
            ))

    if not dry_run:
        save_state(vault_path, state)
        if any(r.action in ("new", "updated") for r in results):
            try:
                rebuild_index(vault_path)
            except Exception:
                # Index rebuild is best-effort; don't fail sync over it.
                pass
    return results


def pending_count(vault_path: Path) -> int:
    """Dry-run sync to count how many sessions would be added/updated."""
    try:
        results = sync(vault_path, dry_run=True)
    except Exception:
        return 0
    return sum(1 for r in results if r.action in ("new", "updated"))


def synced_count(vault_path: Path) -> int:
    return len(load_state(vault_path))
