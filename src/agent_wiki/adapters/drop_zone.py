"""Drop zone adapter.

External producers (e.g. a personal assistant) write conversation bundles
directly into a configured directory. This adapter validates them against
``Doc/conversation-bundle-schema.md`` and moves valid ones into
``<vault>/raw/sessions/``. Malformed bundles are quarantined under
``<drop_zone>/rejected/`` with a ``.reason`` sidecar.

Unlike the other adapters, this one mutates the filesystem inside
``to_bundle``: moving the file out of the drop zone is what makes a given
bundle "ingested" from the producer's perspective. The fingerprint is the
drop-zone filename itself so sync won't re-process a file once it has been
moved.
"""
from __future__ import annotations

import hashlib
import shutil

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agent_wiki.adapters import ConversationAdapter
from agent_wiki.conversation import BUNDLE_SUBDIR, Conversation, read_bundle


@dataclass
class DropZoneRef:
    path: Path

    def __str__(self) -> str:
        return f"drop-zone:{self.path.name}"


class DropZoneAdapter(ConversationAdapter):
    name = "drop-zone"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._vault_path: Path | None = None
        self._drop_zone: Path | None = None

        path = self.config.get("path")
        if path:
            p = Path(path).expanduser()
            if p.is_absolute():
                self._drop_zone = p
            else:
                # Resolve against vault later if we haven't been given one yet.
                self._relative = p
        else:
            self._relative = Path("incoming")

    # The sync layer passes vault_path implicitly by constructing the adapter
    # after load_vault_config; we resolve the relative drop zone lazily.
    def _resolve_zone(self) -> Path:
        if self._drop_zone is not None:
            return self._drop_zone
        if self._vault_path is None:
            # Fall back to CWD — adapters may also be used outside of sync.
            return Path.cwd() / self._relative
        return self._vault_path / self._relative

    def set_vault(self, vault_path: Path) -> None:
        self._vault_path = vault_path

    def discover(self) -> Iterable[DropZoneRef]:
        zone = self._resolve_zone()
        if not zone.exists():
            return
        for p in sorted(zone.glob("*.md")):
            if not p.is_file():
                continue
            yield DropZoneRef(path=p)

    def session_key(self, ref: DropZoneRef) -> str:
        meta = _read_frontmatter_header(ref.path)
        if meta.get("type") != "conversation":
            raise ValueError(
                f"{ref.path}: not a conversation bundle (type={meta.get('type')!r})"
            )
        missing = [k for k in ("agent", "session_id") if not meta.get(k)]
        if missing:
            raise ValueError(
                f"{ref.path}: bundle missing required frontmatter: {', '.join(missing)}"
            )
        return f"{meta['agent']}:{meta['session_id']}"

    def fingerprint(self, ref: DropZoneRef) -> str:
        # Content hash so re-dropping a file with the same name but different
        # content is treated as an update.
        return f"sha1:{_sha1(ref.path)}"

    def to_bundle(self, ref: DropZoneRef) -> Conversation:
        zone = self._resolve_zone()
        try:
            conv = read_bundle(ref.path)
        except ValueError as e:
            _quarantine(zone, ref.path, str(e))
            raise

        if self._vault_path is not None:
            dest_dir = self._vault_path / BUNDLE_SUBDIR
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{conv.bundle_id()}.md"
            shutil.move(str(ref.path), str(dest))

        return conv


_MAX_HEADER_LINES = 200


def _read_frontmatter_header(path: Path) -> dict:
    """Parse only the leading ``---`` frontmatter block, stopping at its close.

    The body is never read, so the cost is independent of transcript size.
    """
    lines: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        first = f.readline()
        if first.strip() != "---":
            return {}
        for line in f:
            if line.strip() == "---":
                break
            lines.append(line)
            if len(lines) > _MAX_HEADER_LINES:
                return {}  # unterminated frontmatter; do not scan the body
    try:
        meta = yaml.safe_load("".join(lines))
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _quarantine(zone: Path, path: Path, reason: str) -> None:
    rej_dir = zone / "rejected"
    rej_dir.mkdir(parents=True, exist_ok=True)
    target = rej_dir / path.name
    try:
        shutil.move(str(path), str(target))
    except Exception:
        return
    (target.with_suffix(target.suffix + ".reason")).write_text(reason + "\n")
