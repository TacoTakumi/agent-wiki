import pytest
from agent_wiki.lint import lint_vault
from agent_wiki.page import render_page


def _create_page(vault, topic, slug, title, body, tags=None, sources=None):
    meta = {
        "title": title,
        "topic": topic,
        "tags": tags or [],
        "created": "2026-04-14",
        "updated": "2026-04-14",
        "sources": sources or [],
    }
    page_path = vault / topic / f"{slug}.md"
    page_path.write_text(render_page(meta, body))
    return page_path


def test_lint_broken_wikilinks(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Notes",
                 "# Notes\n\nSee [[Nonexistent Page]] for details.\n")

    issues = lint_vault(tmp_vault)
    broken = [i for i in issues if i["type"] == "broken_wikilink"]
    assert len(broken) == 1
    assert "Nonexistent Page" in broken[0]["detail"]


def test_lint_valid_wikilinks(tmp_vault):
    _create_page(tmp_vault, "research", "notes", "Notes",
                 "# Notes\n\nSee [[Docker]] for details.\n")
    _create_page(tmp_vault, "tools", "docker", "Docker",
                 "# Docker\n\nDocker content.\n")

    issues = lint_vault(tmp_vault)
    broken = [i for i in issues if i["type"] == "broken_wikilink"]
    assert len(broken) == 0


def test_lint_orphan_pages(tmp_vault):
    _create_page(tmp_vault, "research", "orphan", "Orphan Page",
                 "# Orphan Page\n\nNo one links here.\n")

    issues = lint_vault(tmp_vault)
    orphans = [i for i in issues if i["type"] == "orphan"]
    assert len(orphans) == 1


def test_lint_un_ingested_raw(tmp_vault):
    (tmp_vault / "raw" / "stray-file.md").write_text("# Stray\n")

    issues = lint_vault(tmp_vault)
    raw_issues = [i for i in issues if i["type"] == "raw_not_ingested"]
    assert len(raw_issues) == 1
    assert "stray-file.md" in raw_issues[0]["detail"]


def test_lint_ignores_sidecar_files(tmp_vault, tmp_path):
    # A freshly-ingested file writes raw/<name>.meta.yaml; that provenance sidecar
    # is metadata, never an un-ingested raw source nor an orphan.
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nbody\n")
    ingest_file(src, tmp_vault, topic="research")
    assert (tmp_vault / "raw" / "note.meta.yaml").exists()  # guard the premise

    issues = lint_vault(tmp_vault)
    offending = [i for i in issues
                 if i["path"].endswith(".meta.yaml") or "meta.yaml" in i.get("detail", "")]
    assert offending == [], offending


def test_lint_missing_frontmatter(tmp_vault):
    page_path = tmp_vault / "research" / "bare.md"
    page_path.write_text("# No Frontmatter\n\nJust content.\n")

    issues = lint_vault(tmp_vault)
    fm_issues = [i for i in issues if i["type"] == "missing_frontmatter"]
    assert len(fm_issues) == 1


def test_lint_raw_page_drift_detected(tmp_vault, tmp_path):
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "d.md"
    src.write_text("# D\n\noriginal\n")
    page = ingest_file(src, tmp_vault, topic="research")
    # No drift right after ingest.
    assert [i for i in lint_vault(tmp_vault) if i["type"] == "raw_page_drift"] == []
    # Hand-edit the page -> drift.
    page.write_text(page.read_text().replace("original", "edited by hand"))
    drift = [i for i in lint_vault(tmp_vault) if i["type"] == "raw_page_drift"]
    assert len(drift) == 1
    assert "raw/d.md" in drift[0]["detail"]


def test_lint_raw_page_drift_skips_binary(tmp_vault):
    (tmp_vault / "raw" / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    (tmp_vault / "research" / "blob.md").write_text(
        "---\ntitle: Blob\ntopic: research\nsources:\n- raw/blob.bin\n---\n\nbody\n"
    )
    drift = [i for i in lint_vault(tmp_vault) if i["type"] == "raw_page_drift"]
    assert drift == []
