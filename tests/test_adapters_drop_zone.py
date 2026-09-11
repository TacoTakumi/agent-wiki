from pathlib import Path

import pytest
import yaml

from agent_wiki.adapters.drop_zone import DropZoneAdapter, DropZoneRef
from agent_wiki.conversation import BUNDLE_SUBDIR, read_bundle
from agent_wiki.sync import sync


VALID_BUNDLE = """---
type: conversation
agent: my-assistant
session_id: 2026-04-18-1030
title: Quick chat about redis
---

# Quick chat about redis

## user
Why is MULTI blocking?

## assistant
Because ...
"""


MALFORMED_BUNDLE = """---
type: document
title: nope
---

body
"""


def _configure_vault(tmp_vault: Path, drop_zone_abs: Path) -> None:
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    if "sessions" not in config["topics"]:
        config["topics"].append("sessions")
    config["conversations"] = {"topic": "sessions"}
    config["sources"] = {
        "claude_code": {"enabled": False},
        "opencode": {"enabled": False},
        "drop_zone": {"enabled": True, "path": str(drop_zone_abs)},
    }
    config["summarizer"] = {"type": "none"}
    (tmp_vault / "wiki.yaml").write_text(yaml.dump(config))
    (tmp_vault / "sessions").mkdir(exist_ok=True)


def test_discover_and_convert(tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "one.md").write_text(VALID_BUNDLE)

    adapter = DropZoneAdapter({"path": str(zone)})
    adapter.set_vault(tmp_path / "vault")
    (tmp_path / "vault" / "raw" / "sessions").mkdir(parents=True)

    refs = list(adapter.discover())
    assert len(refs) == 1
    fp = adapter.fingerprint(refs[0])
    assert fp.startswith("sha1:")

    conv = adapter.to_bundle(refs[0])
    assert conv.agent == "my-assistant"
    assert conv.session_id == "2026-04-18-1030"
    # File was moved out of the drop zone and into raw/sessions/
    assert not (zone / "one.md").exists()
    moved = tmp_path / "vault" / "raw" / "sessions" / f"{conv.bundle_id()}.md"
    assert moved.exists()


def test_malformed_quarantined(tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "bad.md").write_text(MALFORMED_BUNDLE)

    adapter = DropZoneAdapter({"path": str(zone)})
    adapter.set_vault(tmp_path / "vault")
    (tmp_path / "vault" / "raw" / "sessions").mkdir(parents=True)

    refs = list(adapter.discover())
    with pytest.raises(ValueError):
        adapter.to_bundle(refs[0])

    assert not (zone / "bad.md").exists()
    rejected = zone / "rejected" / "bad.md"
    assert rejected.exists()
    assert (zone / "rejected" / "bad.md.reason").exists()


def test_sync_with_drop_zone(tmp_vault, tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "one.md").write_text(VALID_BUNDLE)
    _configure_vault(tmp_vault, zone)

    results = sync(tmp_vault)
    assert [r.action for r in results] == ["new"]

    # Bundle moved into vault
    bundles = list((tmp_vault / BUNDLE_SUBDIR).glob("*.md"))
    assert len(bundles) == 1

    # Drop zone now empty of valid bundles
    assert not (zone / "one.md").exists()

    # Wiki page exists
    assert results[0].page is not None
    assert results[0].page.parent.name == "sessions"


def test_sync_drop_zone_malformed_quarantines(tmp_vault, tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "bad.md").write_text(MALFORMED_BUNDLE)
    _configure_vault(tmp_vault, zone)

    results = sync(tmp_vault)
    assert len(results) == 1
    assert results[0].action == "error"
    assert "not a conversation bundle" in (results[0].error or "")
    # File was quarantined
    assert (zone / "rejected" / "bad.md").exists()


def test_sync_drop_zone_rerun_after_update(tmp_vault, tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "one.md").write_text(VALID_BUNDLE)
    _configure_vault(tmp_vault, zone)

    sync(tmp_vault)

    # Drop it again with different content — simulates producer re-emitting
    updated = VALID_BUNDLE.replace("blocking?", "blocking in cluster mode?")
    (zone / "one.md").write_text(updated)

    results = sync(tmp_vault)
    assert [r.action for r in results] == ["updated"]


def test_session_key_reads_only_frontmatter_header(tmp_path, monkeypatch):
    zone = tmp_path / "incoming"
    zone.mkdir()
    big_body = "\n".join(f"filler line {i}" for i in range(20000))
    (zone / "one.md").write_text(VALID_BUNDLE + big_body + "\n")

    adapter = DropZoneAdapter({"path": str(zone)})
    refs = list(adapter.discover())

    import builtins
    real_open = builtins.open
    consumed: list[int] = []

    class _Counting:
        def __init__(self, fh):
            self._fh = fh

        def __iter__(self):
            for line in self._fh:
                consumed.append(len(line))
                yield line

        def readline(self, *a):
            line = self._fh.readline(*a)
            consumed.append(len(line))
            return line

        def read(self, *a):
            data = self._fh.read(*a)
            consumed.append(len(data))
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def _counting_open(*args, **kwargs):
        return _Counting(real_open(*args, **kwargs))

    monkeypatch.setattr(builtins, "open", _counting_open)
    try:
        key = adapter.session_key(refs[0])
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert key == "my-assistant:2026-04-18-1030"
    assert sum(consumed) < len(VALID_BUNDLE) + 64

    adapter.set_vault(tmp_path / "vault")
    (tmp_path / "vault" / "raw" / "sessions").mkdir(parents=True)
    conv = adapter.to_bundle(refs[0])
    assert key == f"{conv.agent}:{conv.session_id}"


def test_sync_dry_run_leaves_drop_zone_file_in_place(tmp_vault, tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "one.md").write_text(VALID_BUNDLE)
    _configure_vault(tmp_vault, zone)

    results = sync(tmp_vault, dry_run=True)
    assert [r.action for r in results] == ["new"]
    assert results[0].key == "my-assistant:2026-04-18-1030"
    assert (zone / "one.md").exists()
    assert not list((tmp_vault / BUNDLE_SUBDIR).glob("*.md"))


def test_sync_identical_redrop_is_skipped_but_moved_out(tmp_vault, tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "one.md").write_text(VALID_BUNDLE)
    _configure_vault(tmp_vault, zone)
    sync(tmp_vault)
    assert not (zone / "one.md").exists()

    (zone / "one.md").write_text(VALID_BUNDLE)
    results = sync(tmp_vault)
    assert [r.action for r in results] == ["skipped"]
    assert not (zone / "one.md").exists()
    assert len(list((tmp_vault / BUNDLE_SUBDIR).glob("*.md"))) == 1


def test_sync_dry_run_reports_malformed_bundle_as_error_without_quarantine(tmp_vault, tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    (zone / "bad.md").write_text(MALFORMED_BUNDLE)
    _configure_vault(tmp_vault, zone)

    results = sync(tmp_vault, dry_run=True)
    assert [r.action for r in results] == ["error"]
    assert "not a conversation bundle" in (results[0].error or "")
    assert (zone / "bad.md").exists()
    assert not (zone / "rejected").exists()


def test_session_key_matches_read_bundle_verdicts(tmp_path):
    zone = tmp_path / "incoming"
    zone.mkdir()
    adapter = DropZoneAdapter({"path": str(zone)})

    # Unterminated frontmatter: both reject it as not a bundle.
    body = "\n".join(f"line {i}" for i in range(5000))
    open_md = zone / "open.md"
    open_md.write_text("---\ntype: conversation\nagent: a\nsession_id: s\n" + body)
    with pytest.raises(ValueError):
        adapter.session_key(DropZoneRef(path=open_md))
    with pytest.raises(ValueError):
        read_bundle(open_md)

    # Missing title: read_bundle requires it, so the cheap key must too.
    untitled = zone / "untitled.md"
    untitled.write_text("---\ntype: conversation\nagent: a\nsession_id: s\n---\n\nbody\n")
    with pytest.raises(ValueError, match="title"):
        adapter.session_key(DropZoneRef(path=untitled))
    with pytest.raises(ValueError, match="title"):
        read_bundle(untitled)

    # A very long but well-formed header is still accepted by both.
    extra = "\n".join(f"k{i}: v{i}" for i in range(300))
    long_md = zone / "long.md"
    long_md.write_text(
        f"---\ntype: conversation\nagent: a\nsession_id: s\ntitle: t\n{extra}\n---\n\nbody\n"
    )
    assert adapter.session_key(DropZoneRef(path=long_md)) == "a:s"
    assert read_bundle(long_md).session_id == "s"
