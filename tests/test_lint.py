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


def test_lint_source_drift_detected(tmp_vault, tmp_path):
    # A raw edited in place no longer matches the sha256 its sidecar recorded.
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "s.md"
    src.write_text("# S\n\noriginal\n")
    ingest_file(src, tmp_vault, topic="research")
    raw = tmp_vault / "raw" / "s.md"
    # Unedited: recomputed sha256 still matches the sidecar -> not flagged.
    assert [i for i in lint_vault(tmp_vault) if i["type"] == "source_drift"] == []
    # Edit the raw body in place -> recomputed sha256 diverges from the sidecar.
    raw.write_text(raw.read_text().replace("original", "tampered"))
    drift = [i for i in lint_vault(tmp_vault) if i["type"] == "source_drift"]
    assert len(drift) == 1
    assert drift[0]["path"] == "raw/s.md"


def test_lint_source_drift_unedited_not_flagged(tmp_vault, tmp_path):
    # A freshly-ingested, untouched raw matches its sidecar sha256 exactly.
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "u.md"
    src.write_text("# U\n\nbody\n")
    ingest_file(src, tmp_vault, topic="research")
    assert [i for i in lint_vault(tmp_vault) if i["type"] == "source_drift"] == []


def test_lint_source_drift_distinct_from_raw_page_drift(tmp_vault, tmp_path):
    # Editing a raw in place fires BOTH checks; the two issues stay clear --
    # source_drift points at the raw (it changed), raw_page_drift at the page.
    from agent_wiki.ingest import ingest_file
    src = tmp_path / "b.md"
    src.write_text("# B\n\noriginal\n")
    ingest_file(src, tmp_vault, topic="research")
    raw = tmp_vault / "raw" / "b.md"
    raw.write_text(raw.read_text().replace("original", "tampered in raw"))
    issues = lint_vault(tmp_vault)
    src_drift = [i for i in issues if i["type"] == "source_drift"]
    page_drift = [i for i in issues if i["type"] == "raw_page_drift"]
    assert len(src_drift) == 1
    assert len(page_drift) == 1
    assert src_drift[0]["path"] == "raw/b.md"
    assert page_drift[0]["path"] != "raw/b.md"


# --- opt-in URL re-fetch (REQ-20) -------------------------------------------

class _FakeFetcher:
    """Records calls and returns fixed bytes — stands in for the network."""
    def __init__(self, body: bytes):
        self.body = body
        self.calls = 0

    def fetch(self, url):
        from agent_wiki.fetch import FetchResult
        self.calls += 1
        return FetchResult(body=self.body, content_type="text/html", source_url=url)


class _BoomFetcher:
    """Any fetch is a test failure — proves a code path made no network call."""
    def fetch(self, url):
        raise AssertionError(f"unexpected network fetch of {url}")


def _seed_url_raw(vault, name="page.md", url="https://example.com/page",
                  upstream=b"<html>original upstream</html>"):
    """A URL-ingested raw + sidecar: records the source URL and the asset sha256
    of the originally-fetched bytes (what re-fetch compares against)."""
    from agent_wiki.page import save_sidecar, sha256_bytes
    raw = vault / "raw" / name
    raw.write_text("# Page\n\nextracted body\n")
    save_sidecar(raw, {
        "source": url,
        "fetcher": "http",
        "ingested": "2026-06-01T00:00:00",
        "sha256": sha256_bytes(raw.read_bytes()),
        "asset_sha256": sha256_bytes(upstream),
    })
    return raw


def test_lint_refetch_flags_upstream_changed(tmp_vault):
    _seed_url_raw(tmp_vault, upstream=b"<html>original upstream</html>")
    changed = _FakeFetcher(b"<html>CHANGED upstream</html>")
    issues = lint_vault(tmp_vault, refetch=True, fetcher=changed)
    up = [i for i in issues if i["type"] == "upstream_changed"]
    assert len(up) == 1
    assert up[0]["path"] == "raw/page.md"
    assert changed.calls == 1


def test_lint_refetch_unchanged_not_flagged(tmp_vault):
    upstream = b"<html>original upstream</html>"
    _seed_url_raw(tmp_vault, upstream=upstream)
    same = _FakeFetcher(upstream)
    issues = lint_vault(tmp_vault, refetch=True, fetcher=same)
    assert [i for i in issues if i["type"] == "upstream_changed"] == []
    assert same.calls == 1  # it did fetch; the body just matched


def test_lint_without_refetch_makes_no_network(tmp_vault):
    _seed_url_raw(tmp_vault)
    # Default (refetch off): the fetcher must never be touched, even when supplied.
    issues = lint_vault(tmp_vault, fetcher=_BoomFetcher())
    assert [i for i in issues if i["type"] == "upstream_changed"] == []


# --- stale-content (REQ-21) --------------------------------------------------

def _seed_sourced_page(vault, slug, updated, ingested):
    """A page with sources whose sidecars carry the given ``ingested`` dates.
    ``ingested`` is a list of ISO date/datetime strings (one raw+sidecar each)."""
    from agent_wiki.page import save_sidecar, sha256_bytes, render_page
    sources = []
    for i, ing in enumerate(ingested):
        raw = vault / "raw" / f"{slug}-{i}.md"
        raw.write_text(f"# {slug} {i}\n\nbody\n")
        save_sidecar(raw, {"source": f"file:{slug}-{i}", "fetcher": "file",
                           "ingested": ing, "sha256": sha256_bytes(raw.read_bytes())})
        sources.append(f"raw/{slug}-{i}.md")
    meta = {"title": slug, "topic": "research", "tags": [],
            "created": "2026-01-01", "updated": updated, "sources": sources}
    (vault / "research" / f"{slug}.md").write_text(
        render_page(meta, f"# {slug}\n\nbody\n"))


def test_lint_stale_content_flagged(tmp_vault):
    from datetime import date, timedelta
    newest = date(2026, 6, 1)
    updated = newest - timedelta(days=91)  # 91 days behind -> stale
    _seed_sourced_page(tmp_vault, "old", updated.isoformat(), [newest.isoformat()])
    stale = [i for i in lint_vault(tmp_vault) if i["type"] == "stale_content"]
    assert len(stale) == 1
    assert stale[0]["path"] == "research/old.md"


def test_lint_stale_content_within_90_not_flagged(tmp_vault):
    from datetime import date, timedelta
    newest = date(2026, 6, 1)
    updated = newest - timedelta(days=90)  # exactly 90 -> not stale
    _seed_sourced_page(tmp_vault, "fresh", updated.isoformat(), [newest.isoformat()])
    stale = [i for i in lint_vault(tmp_vault) if i["type"] == "stale_content"]
    assert stale == []


def test_lint_stale_content_uses_newest_source(tmp_vault):
    # Page updated 2026-03-01; one source is 14 days newer (not stale on its own),
    # another is far newer -> the newest source decides, so the page is stale.
    _seed_sourced_page(tmp_vault, "multi", "2026-03-01",
                       ["2026-03-15T00:00:00", "2026-09-01T00:00:00"])
    stale = [i for i in lint_vault(tmp_vault) if i["type"] == "stale_content"]
    assert len(stale) == 1
    assert stale[0]["path"] == "research/multi.md"
