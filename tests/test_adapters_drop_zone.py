from pathlib import Path

import pytest
import yaml

from agent_wiki.adapters.drop_zone import DropZoneAdapter
from agent_wiki.conversation import BUNDLE_SUBDIR
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
