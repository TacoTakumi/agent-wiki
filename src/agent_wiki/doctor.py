"""Doctor: inspect a vault for drift from the current schema and fix it.

Each ``Check`` detects one specific issue and knows how to repair it. The CLI
steps through them in order and asks before applying each fix — so a vault
that predates new schema additions (new topics, new ``sources`` block, new
default directories) can be brought up to date without hand-editing YAML.

Checks are intentionally narrow: one issue per class, idempotent, and
independent of each other. Adding a check when the schema grows is one class
plus one entry in ``ALL_CHECKS``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_wiki.config import load_vault_config
from agent_wiki.page import (
    parse_page, page_body_for_raw, page_raw_diverged,
    load_sidecar, save_sidecar, sha256_bytes,
    render_hash, update_frontmatter,
)
from agent_wiki.vault import DEFAULT_TOPICS, _default_sources_config


@dataclass
class Finding:
    check: "Check"
    detail: str


class Check(ABC):
    """One thing the doctor can inspect and optionally repair."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def detect(self, vault_path: Path) -> Finding | None:
        """Return a Finding if the issue is present, else None."""

    @abstractmethod
    def fix(self, vault_path: Path) -> str:
        """Apply the fix. Return a short description of what changed."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_config(vault_path: Path) -> dict[str, Any]:
    return yaml.safe_load((vault_path / "wiki.yaml").read_text()) or {}


def _write_config(vault_path: Path, config: dict[str, Any]) -> None:
    (vault_path / "wiki.yaml").write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


class MissingSessionsTopic(Check):
    name = "sessions-topic"
    description = "Add the 'sessions' topic to wiki.yaml"

    def detect(self, vault_path: Path) -> Finding | None:
        config = _read_config(vault_path)
        if "sessions" in (config.get("topics") or []):
            return None
        return Finding(self, "wiki.yaml topics list is missing 'sessions'")

    def fix(self, vault_path: Path) -> str:
        config = _read_config(vault_path)
        topics = list(config.get("topics") or [])
        if "sessions" not in topics:
            topics.append("sessions")
        config["topics"] = topics
        _write_config(vault_path, config)
        return "added 'sessions' to topics"


class MissingConversationsBlock(Check):
    name = "conversations-block"
    description = "Add the 'conversations' config block"

    def detect(self, vault_path: Path) -> Finding | None:
        config = _read_config(vault_path)
        if "conversations" in config:
            return None
        return Finding(self, "wiki.yaml is missing the 'conversations' block")

    def fix(self, vault_path: Path) -> str:
        config = _read_config(vault_path)
        config["conversations"] = {"topic": "sessions", "include_live": False}
        _write_config(vault_path, config)
        return "added conversations block"


class MissingSummarizerBlock(Check):
    name = "summarizer-block"
    description = "Add the 'summarizer' config block (default: none)"

    def detect(self, vault_path: Path) -> Finding | None:
        config = _read_config(vault_path)
        if "summarizer" in config:
            return None
        return Finding(self, "wiki.yaml is missing the 'summarizer' block")

    def fix(self, vault_path: Path) -> str:
        config = _read_config(vault_path)
        config["summarizer"] = {"type": "none"}
        _write_config(vault_path, config)
        return "added summarizer block (type: none)"


class MissingSourcesBlock(Check):
    name = "sources-block"
    description = "Add the 'sources' config block with default adapters"

    def detect(self, vault_path: Path) -> Finding | None:
        config = _read_config(vault_path)
        if "sources" in config:
            return None
        return Finding(self, "wiki.yaml is missing the 'sources' block")

    def fix(self, vault_path: Path) -> str:
        config = _read_config(vault_path)
        config["sources"] = _default_sources_config()
        _write_config(vault_path, config)
        return "added sources block (claude-code, opencode, drop-zone)"


class MissingTopicDirs(Check):
    name = "topic-dirs"
    description = "Create missing topic directories listed in wiki.yaml"

    def detect(self, vault_path: Path) -> Finding | None:
        config = _read_config(vault_path)
        topics = config.get("topics") or []
        missing = [t for t in topics if not (vault_path / t).is_dir()]
        if not missing:
            return None
        return Finding(self, f"missing topic dirs: {', '.join(missing)}")

    def fix(self, vault_path: Path) -> str:
        config = _read_config(vault_path)
        created = []
        for t in config.get("topics") or []:
            d = vault_path / t
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
                created.append(t)
        return f"created dirs: {', '.join(created)}" if created else "no dirs created"


class MissingRawSessionsDir(Check):
    name = "raw-sessions-dir"
    description = "Create raw/sessions/ for conversation bundles"

    def detect(self, vault_path: Path) -> Finding | None:
        if (vault_path / "raw" / "sessions").is_dir():
            return None
        return Finding(self, "raw/sessions/ does not exist")

    def fix(self, vault_path: Path) -> str:
        (vault_path / "raw" / "sessions").mkdir(parents=True, exist_ok=True)
        return "created raw/sessions/"


class MissingDropZoneDir(Check):
    name = "drop-zone-dir"
    description = "Create the drop-zone directory for external bundles"

    def detect(self, vault_path: Path) -> Finding | None:
        try:
            config = _read_config(vault_path)
        except Exception:
            return None
        dz = ((config.get("sources") or {}).get("drop_zone") or {})
        if not dz.get("enabled"):
            return None
        path = Path(dz.get("path") or "incoming").expanduser()
        full = path if path.is_absolute() else (vault_path / path)
        if full.is_dir():
            return None
        return Finding(self, f"drop zone path does not exist: {full}")

    def fix(self, vault_path: Path) -> str:
        config = _read_config(vault_path)
        dz = (config.get("sources") or {}).get("drop_zone") or {}
        path = Path(dz.get("path") or "incoming").expanduser()
        full = path if path.is_absolute() else (vault_path / path)
        full.mkdir(parents=True, exist_ok=True)
        return f"created {full}"


class SourcePathMissing(Check):
    """Warn (don't fix) when an enabled source points somewhere that doesn't exist."""

    name = "source-path-missing"
    description = "Check that enabled source paths exist"

    def detect(self, vault_path: Path) -> Finding | None:
        config = _read_config(vault_path)
        sources = config.get("sources") or {}
        problems: list[str] = []
        for key in ("claude_code", "opencode"):
            s = sources.get(key) or {}
            if not s.get("enabled"):
                continue
            path_key = "db_path" if key == "opencode" else "path"
            raw = s.get(path_key)
            if not raw:
                continue
            p = Path(raw).expanduser()
            if not p.exists():
                problems.append(f"{key}.{path_key}={p}")
        if not problems:
            return None
        return Finding(self, "enabled sources point at missing paths: " + "; ".join(problems))

    def fix(self, vault_path: Path) -> str:
        # Warn-only — don't auto-disable; user may be mid-install.
        return "no change (informational)"


class RawContentDrift(Check):
    """Rewrite raw/ files that drifted from their (canonical) wiki page."""

    name = "raw-content-drift"
    description = "Rewrite raw/ files that drifted from their (canonical) page"

    def _drifted(self, vault_path: Path) -> list[tuple[Path, str]]:
        config = _read_config(vault_path)
        out: list[tuple[Path, str]] = []
        for topic in config.get("topics") or []:
            topic_dir = vault_path / topic
            if not topic_dir.is_dir():
                continue
            for md_file in topic_dir.rglob("*.md"):
                page = parse_page(md_file)
                for src in (page["meta"].get("sources") or []):
                    if not src.startswith("raw/"):
                        continue
                    raw_path = vault_path / src
                    if not raw_path.is_file():
                        continue
                    try:
                        raw_text = raw_path.read_text()
                    except UnicodeDecodeError:
                        continue  # binary raw isn't text-comparable; skip like a missing source
                    if page_raw_diverged(page["body"], raw_text):
                        out.append((raw_path, page_body_for_raw(page["body"])))
        return out

    def detect(self, vault_path: Path) -> Finding | None:
        drifted = self._drifted(vault_path)
        if not drifted:
            return None
        names = ", ".join(sorted(p.name for p, _ in drifted))
        return Finding(self, f"{len(drifted)} raw file(s) differ from their page: {names}")

    def fix(self, vault_path: Path) -> str:
        drifted = self._drifted(vault_path)
        for raw_path, canonical in drifted:
            raw_path.write_text(canonical)
            # Keep the sidecar sha256 in lock-step with the rewritten body
            # (REQ-02): a stale hash would otherwise make lint's source_drift
            # check false-fire on a raw this very fix just reconciled.
            meta = load_sidecar(raw_path)
            if meta:
                meta["sha256"] = sha256_bytes(raw_path.read_bytes())
                save_sidecar(raw_path, meta)
        return f"rewrote {len(drifted)} raw file(s) from pages"


class RenderHashUnstamped(Check):
    """Stamp render_hash on un-hashed pages whose body still matches their raw.

    Migration for vaults written before render_hash existed (REQ-07): a page that
    lacks the fingerprint but is still faithful to its raw (``page_raw_diverged``
    is False) gets stamped, giving the reingest drift guard a baseline. The value
    written is exactly what the ingest/reingest write path stamps — ``render_hash``
    of the page's own on-disk body — so a stamped page is guard-clean.

    Two deliberate exclusions keep the migration honest:

    - **Divergent pages are skipped.** An un-hashed page whose body has drifted
      from its raw is a pre-existing hand-edit; stamping it would silently adopt
      that edit as the baseline. It is left for the separate divergent-report
      check (REQ-08) to surface for review.
    - **Pages with no readable raw source are skipped** — there is nothing to
      prove faithfulness against, and such a page cannot be reingested anyway, so
      it needs no baseline.

    Preview-by-default and applied only under ``--fix`` (REQ-09); the CLI/service
    gate it behind the fix flag, never an interactive confirm.
    """

    name = "render-hash-unstamped"
    description = "Stamp render_hash on un-hashed pages that match their raw"

    def _faithful_to_raw(self, vault_path: Path, page: dict) -> bool:
        for src in (page["meta"].get("sources") or []):
            if not src.startswith("raw/"):
                continue
            raw_path = vault_path / src
            if not raw_path.is_file():
                continue
            try:
                raw_text = raw_path.read_text()
            except UnicodeDecodeError:
                continue  # binary raw isn't text-comparable; treat as no source
            return not page_raw_diverged(page["body"], raw_text)
        return False  # no comparable raw -> nothing to prove faithfulness against

    def _pending(self, vault_path: Path) -> list[Path]:
        config = _read_config(vault_path)
        out: list[Path] = []
        for topic in config.get("topics") or []:
            topic_dir = vault_path / topic
            if not topic_dir.is_dir():
                continue
            for md_file in topic_dir.rglob("*.md"):
                page = parse_page(md_file)
                if page["meta"].get("render_hash"):
                    continue  # already stamped
                if self._faithful_to_raw(vault_path, page):
                    out.append(md_file)
        return out

    def detect(self, vault_path: Path) -> Finding | None:
        pending = self._pending(vault_path)
        if not pending:
            return None
        names = ", ".join(sorted(p.name for p in pending))
        return Finding(
            self, f"{len(pending)} page(s) lack render_hash and match their raw: {names}"
        )

    def fix(self, vault_path: Path) -> str:
        pending = self._pending(vault_path)
        for md_file in pending:
            parsed = parse_page(md_file)
            meta = parsed["meta"]
            # Hash the page's own on-disk body — the same value ingest/reingest
            # stamps — and splice it back via update_frontmatter so the body stays
            # byte-identical (never trips the guard on the very page it stamps).
            meta["render_hash"] = render_hash(parsed["body"])
            update_frontmatter(md_file, meta)
        return f"stamped render_hash on {len(pending)} page(s)"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


ALL_CHECKS: tuple[Check, ...] = (
    MissingSessionsTopic(),
    MissingConversationsBlock(),
    MissingSummarizerBlock(),
    MissingSourcesBlock(),
    MissingTopicDirs(),
    MissingRawSessionsDir(),
    MissingDropZoneDir(),
    SourcePathMissing(),
    RawContentDrift(),
    RenderHashUnstamped(),
)


def run_checks(vault_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        try:
            f = check.detect(vault_path)
        except Exception as e:
            # Checks should be robust, but don't let a broken check block others.
            findings.append(Finding(check, f"check failed: {e!r}"))
            continue
        if f is not None:
            findings.append(f)
    return findings
