import hashlib
import yaml
import pytest
from pathlib import Path
from agent_wiki.ingest import ingest_file
from agent_wiki.page import parse_page, render_page


def test_ingest_copies_to_raw(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nSome content here.\n")

    ingest_file(source, tmp_vault, topic="research")

    assert (tmp_vault / "raw" / "notes.md").exists()
    assert (tmp_vault / "raw" / "notes.md").read_text() == source.read_text()


def test_ingest_creates_wiki_page(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nSome content here.\n")

    result = ingest_file(source, tmp_vault, topic="research")

    assert result.exists()
    assert result.parent.name == "research"

    page = parse_page(result)
    assert page["meta"]["title"] == "My Notes"
    assert page["meta"]["topic"] == "research"
    assert page["meta"]["sources"] == ["raw/notes.md"]
    assert "Some content here." in page["body"]


def test_ingest_with_tags(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nContent.\n")

    result = ingest_file(source, tmp_vault, topic="tools", tags=["python", "cli"])

    page = parse_page(result)
    assert page["meta"]["tags"] == ["python", "cli"]


def test_ingest_uses_default_topic(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nContent.\n")

    result = ingest_file(source, tmp_vault)

    assert result.parent.name == "research"


def test_ingest_title_from_filename(tmp_vault, tmp_path):
    source = tmp_path / "my-great-notes.md"
    source.write_text("No heading here, just text.\n")

    result = ingest_file(source, tmp_vault)

    page = parse_page(result)
    assert page["meta"]["title"] == "my-great-notes"


def test_ingest_appends_to_log(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nContent.\n")

    ingest_file(source, tmp_vault, topic="research")

    log_content = (tmp_vault / "log.md").read_text()
    assert "ingest" in log_content.lower()
    assert "notes.md" in log_content


def test_ingest_nonexistent_file(tmp_vault, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_file(tmp_path / "nope.md", tmp_vault)


def test_ingest_refuses_existing_raw(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nv1\n")
    ingest_file(source, tmp_vault, topic="research")

    other = tmp_path / "other" / "notes.md"
    other.parent.mkdir()
    other.write_text("# Other\n\nclash\n")
    with pytest.raises(FileExistsError):
        ingest_file(other, tmp_vault, topic="research")
    # raw/ untouched by the refused ingest
    assert (tmp_vault / "raw" / "notes.md").read_text() == "# Notes\n\nv1\n"


def test_ingest_writes_sidecar(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nSome content here.\n")

    ingest_file(source, tmp_vault, topic="research")

    raw = tmp_vault / "raw" / "notes.md"
    sidecar = tmp_vault / "raw" / "notes.meta.yaml"
    assert sidecar.exists()
    meta = yaml.safe_load(sidecar.read_text())
    assert meta["source"]
    assert meta["fetcher"] == "local"
    assert meta["ingested"]
    assert meta["sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
    # verbatim-raw invariant: stored raw is byte-identical to the source
    assert raw.read_bytes() == source.read_bytes()


def test_ingest_sidecar_records_origin_path(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nbody\n")

    ingest_file(source, tmp_vault, topic="research")

    meta = yaml.safe_load((tmp_vault / "raw" / "notes.meta.yaml").read_text())
    assert meta["source"] == str(source)


def test_update_refreshes_sidecar_sha256(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nv1\n")
    ingest_file(source, tmp_vault, topic="research")

    source.write_text("# Notes\n\nv2 with a longer body\n")
    ingest_file(source, tmp_vault, topic="research", update=True)

    raw = tmp_vault / "raw" / "notes.md"
    meta = yaml.safe_load((tmp_vault / "raw" / "notes.meta.yaml").read_text())
    assert meta["sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()


def test_reingest_passthrough_preserves_extra_frontmatter(tmp_vault, tmp_path):
    body = "# Note\n\nv1\n"
    src = tmp_path / "note.md"
    src.write_text(body)
    page = ingest_file(src, tmp_vault, topic="research")

    # Inline provenance (source_url) plus an arbitrary custom key land on the page.
    # Re-render with the same body so the page stays faithful to raw (no drift).
    meta = parse_page(page)["meta"]
    meta["source_url"] = "https://example.com/article"
    meta["custom_key"] = "keep-me"
    page.write_text(render_page(meta, body))

    # Reingest from the in-vault raw (body unchanged -> clean reingest, no drift).
    raw = tmp_vault / "raw" / "note.md"
    result = ingest_file(raw, tmp_vault, topic="research", update=True)

    out = parse_page(result)["meta"]
    assert out["source_url"] == "https://example.com/article"
    assert out["custom_key"] == "keep-me"
    # managed keys still rebuilt correctly
    assert out["title"] == "Note"
    assert out["sources"] == ["raw/note.md"]


def test_update_rewrites_linked_page(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nv1\n")
    page = ingest_file(source, tmp_vault, topic="research")
    created = parse_page(page)["meta"]["created"]

    source.write_text("# Notes\n\nv2 updated\n")
    result = ingest_file(source, tmp_vault, topic="research", update=True)

    assert result == page                      # same slug -> same path
    meta = parse_page(result)["meta"]
    assert "v2 updated" in parse_page(result)["body"]
    assert meta["created"] == created          # preserved
    assert meta["updated"]                      # present (today)
    assert meta["sources"] == ["raw/notes.md"]
    assert (tmp_vault / "raw" / "notes.md").read_text() == "# Notes\n\nv2 updated\n"


def test_update_renames_on_title_change(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Old Title\n\nbody\n")
    old_page = ingest_file(source, tmp_vault, topic="research")

    source.write_text("# New Title\n\nbody\n")
    result = ingest_file(source, tmp_vault, topic="research", update=True)

    assert result.name == "new-title.md"
    assert result.exists()
    assert not old_page.exists()               # old slug removed
    assert parse_page(result)["meta"]["sources"] == ["raw/notes.md"]


def test_update_moves_on_topic_change(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nbody\n")
    old_page = ingest_file(source, tmp_vault, topic="research")

    result = ingest_file(source, tmp_vault, topic="tools", update=True)

    assert result.parent.name == "tools"
    assert not old_page.exists()
    assert parse_page(result)["meta"]["topic"] == "tools"


def test_update_keeps_tags_unless_overridden(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nbody\n")
    ingest_file(source, tmp_vault, topic="research", tags=["a", "b"])

    kept = ingest_file(source, tmp_vault, topic="research", update=True)
    assert parse_page(kept)["meta"]["tags"] == ["a", "b"]

    replaced = ingest_file(source, tmp_vault, topic="research",
                           tags=["c"], update=True)
    assert parse_page(replaced)["meta"]["tags"] == ["c"]


def test_update_no_linked_page_creates_fresh(tmp_vault, tmp_path):
    # raw exists but no page references it
    (tmp_vault / "raw" / "orphan.md").write_text("old\n")
    source = tmp_path / "orphan.md"
    source.write_text("# Orphan\n\nfresh\n")

    result = ingest_file(source, tmp_vault, topic="research", update=True)
    assert result.exists()
    assert parse_page(result)["meta"]["sources"] == ["raw/orphan.md"]


def test_update_multiple_linked_pages_errors(tmp_vault, tmp_path):
    (tmp_vault / "raw" / "dup.md").write_text("x\n")
    for name in ("one", "two"):
        (tmp_vault / "research" / f"{name}.md").write_text(
            "---\ntitle: " + name + "\ntopic: research\nsources:\n- raw/dup.md\n---\n\nbody\n"
        )
    source = tmp_path / "dup.md"
    source.write_text("# Dup\n\nnew\n")
    with pytest.raises(ValueError):
        ingest_file(source, tmp_vault, topic="research", update=True)
    assert (tmp_vault / "raw" / "dup.md").read_text() == "x\n"


def test_update_destination_collision_errors(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Old Title\n\nbody\n")
    ingest_file(source, tmp_vault, topic="research")
    # A different page already occupies the slug the new title maps to.
    (tmp_vault / "research" / "new-title.md").write_text(
        "---\ntitle: New Title\ntopic: research\nsources: []\n---\n\nother content\n"
    )
    source.write_text("# New Title\n\nbody\n")
    with pytest.raises(ValueError, match="already exists"):
        ingest_file(source, tmp_vault, topic="research", update=True)
    # neither the colliding page nor raw is clobbered
    assert "other content" in (tmp_vault / "research" / "new-title.md").read_text()
    assert (tmp_vault / "raw" / "notes.md").read_text() == "# Old Title\n\nbody\n"


def test_update_logs_update_action(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nv1\n")
    ingest_file(source, tmp_vault, topic="research")
    source.write_text("# Notes\n\nv2\n")
    ingest_file(source, tmp_vault, topic="research", update=True)

    log = (tmp_vault / "log.md").read_text()
    assert "update: notes.md" in log


def test_service_ingest_update(tmp_vault):
    from agent_wiki.service import LocalVaultService
    svc = LocalVaultService(tmp_vault)
    src = tmp_vault / "in.md"
    src.write_text("# In\n\nv1\n")
    svc.ingest(src, topic="research")
    src.write_text("# In\n\nv2\n")
    out = svc.ingest(src, topic="research", update=True)
    assert out["page"] == "research/in.md"
    assert (tmp_vault / "raw" / "in.md").read_text() == "# In\n\nv2\n"


def test_reingest_samefile_does_not_crash(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    page = ingest_file(src, tmp_vault, topic="research")

    raw = tmp_vault / "raw" / "notes.md"          # the vault's OWN raw path
    result = ingest_file(raw, tmp_vault, topic="research", update=True)
    assert result == page
    assert raw.read_text() == "# Notes\n\nv1\n"   # raw untouched (no copy)
    assert "v1" in parse_page(result)["body"]


def test_update_refuses_diverged_page_without_force(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file, PageDriftError
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    page = ingest_file(src, tmp_vault, topic="research")
    page.write_text(page.read_text().replace("v1", "v1\n\nextra hand detail"))

    src.write_text("# Notes\n\nv2\n")
    with pytest.raises(PageDriftError) as exc:
        ingest_file(src, tmp_vault, topic="research", update=True)
    assert exc.value.diff                         # carries a unified diff
    assert "extra hand detail" in page.read_text()  # nothing overwritten
    assert (tmp_vault / "raw" / "notes.md").read_text() == "# Notes\n\nv1\n"


def test_update_force_overwrites_diverged_page(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    page = ingest_file(src, tmp_vault, topic="research")
    page.write_text(page.read_text().replace("v1", "v1\n\nextra hand detail"))

    src.write_text("# Notes\n\nv2\n")
    result = ingest_file(src, tmp_vault, topic="research", update=True, force=True)
    assert "v2" in parse_page(result)["body"]
    assert "extra hand detail" not in parse_page(result)["body"]
    assert (tmp_vault / "raw" / "notes.md").read_text() == "# Notes\n\nv2\n"


def test_update_clean_page_not_blocked(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    ingest_file(src, tmp_vault, topic="research")     # page faithful to raw
    src.write_text("# Notes\n\nv2\n")
    result = ingest_file(src, tmp_vault, topic="research", update=True)  # no force
    assert "v2" in parse_page(result)["body"]


def test_reingest_after_raw_edit_refuses_then_force(tmp_vault, tmp_path):
    # The reingest case: edit the in-vault raw, then rebuild from it.
    from agent_wiki.ingest import ingest_file, PageDriftError
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n\nv1\n")
    ingest_file(src, tmp_vault, topic="research")
    raw = tmp_vault / "raw" / "notes.md"
    raw.write_text("# Notes\n\nv2 edited in raw\n")    # stale page now diverges

    with pytest.raises(PageDriftError):
        ingest_file(raw, tmp_vault, topic="research", update=True)
    result = ingest_file(raw, tmp_vault, topic="research", update=True, force=True)
    assert "v2 edited in raw" in parse_page(result)["body"]


def test_resolve_raw_exact_prefixed_and_stem(tmp_vault):
    from agent_wiki.ingest import resolve_raw
    target = tmp_vault / "raw" / "doc.md"
    target.write_text("x\n")
    assert resolve_raw(tmp_vault, "doc.md") == target
    assert resolve_raw(tmp_vault, "raw/doc.md") == target
    assert resolve_raw(tmp_vault, "doc") == target          # stem match


def test_resolve_raw_missing_raises(tmp_vault):
    from agent_wiki.ingest import resolve_raw
    with pytest.raises(FileNotFoundError):
        resolve_raw(tmp_vault, "nope")


def test_resolve_raw_ambiguous_raises(tmp_vault):
    from agent_wiki.ingest import resolve_raw
    (tmp_vault / "raw" / "doc.md").write_text("x\n")
    (tmp_vault / "raw" / "doc.txt").write_text("y\n")
    with pytest.raises(ValueError):
        resolve_raw(tmp_vault, "doc")


def test_service_reingest_rebuilds_from_edited_raw_with_force(tmp_vault):
    from agent_wiki.service import LocalVaultService
    svc = LocalVaultService(tmp_vault)
    src = tmp_vault / "n.md"
    src.write_text("# N\n\nv1\n")
    svc.ingest(src, topic="research")
    (tmp_vault / "raw" / "n.md").write_text("# N\n\nv2 from raw\n")   # edit raw
    out = svc.reingest("n", force=True)
    assert out["page"] == "research/n.md"
    assert "v2 from raw" in (tmp_vault / "research" / "n.md").read_text()


def test_service_reingest_refuses_diverged_without_force(tmp_vault):
    from agent_wiki.service import LocalVaultService
    from agent_wiki.ingest import PageDriftError
    svc = LocalVaultService(tmp_vault)
    src = tmp_vault / "n.md"
    src.write_text("# N\n\nv1\n")
    svc.ingest(src, topic="research")
    (tmp_vault / "raw" / "n.md").write_text("# N\n\nv2\n")
    with pytest.raises(PageDriftError):
        svc.reingest("n")


def test_service_reingest_missing_raw_raises(tmp_vault):
    from agent_wiki.service import LocalVaultService
    with pytest.raises(FileNotFoundError):
        LocalVaultService(tmp_vault).reingest("does-not-exist")


from agent_wiki.page import render_hash


def test_ingest_stamps_render_hash_matching_on_disk_body(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nSome content here.\n")

    page = ingest_file(source, tmp_vault, topic="research")

    parsed = parse_page(page)
    assert "render_hash" in parsed["meta"]
    # The stamp is the hash of the page's own on-disk body, not the raw content —
    # so the drift guard recomputes the identical value on a clean page.
    assert parsed["meta"]["render_hash"] == render_hash(parsed["body"])


def test_reingest_recomputes_render_hash_on_changed_body(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nv1\n")
    page = ingest_file(source, tmp_vault, topic="research")
    first = parse_page(page)["meta"]["render_hash"]

    source.write_text("# Notes\n\nv2 changed body\n")
    result = ingest_file(source, tmp_vault, topic="research", update=True)

    parsed = parse_page(result)
    assert parsed["meta"]["render_hash"] == render_hash(parsed["body"])
    assert parsed["meta"]["render_hash"] != first  # changed body -> fresh hash, never stale
