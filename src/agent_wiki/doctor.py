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
from agent_wiki.page import parse_page
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


def _page_body_for_raw(body: str) -> str:
    """Page body as it should appear in raw/: drop the one leading blank line
    that render_page inserts, and normalize to a single trailing newline."""
    if body.startswith("\n"):
        body = body[1:]
    return body.rstrip("\n") + "\n"


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
                canonical = _page_body_for_raw(page["body"])
                for src in (page["meta"].get("sources") or []):
                    if not src.startswith("raw/"):
                        continue
                    raw_path = vault_path / src
                    if not raw_path.is_file():
                        continue
                    raw_text = raw_path.read_text()
                    if canonical != (raw_text.rstrip("\n") + "\n"):
                        out.append((raw_path, canonical))
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
        return f"rewrote {len(drifted)} raw file(s) from pages"


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
