from datetime import date, datetime
from pathlib import Path
from agent_wiki.config import load_vault_config
from agent_wiki.fetch import Fetcher, FetchError, HttpFetcher, is_url
from agent_wiki.page import (
    parse_page, extract_wikilinks, page_raw_diverged, page_lines_lost, is_sidecar,
    load_sidecar, sha256_bytes, page_body_for_raw,
)

STALE_DAYS = 90  # a page more than this many days behind its newest source is stale
PAGE_MAX_LINES = 200  # a page body longer than this is a split candidate


def _as_date(value) -> date | None:
    """Coerce a frontmatter/sidecar date value to a ``date``. Handles ``date``,
    ``datetime`` (YAML parses ISO timestamps into these), and ISO strings;
    returns None for anything unparseable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def lint_vault(vault_path: Path, *, refetch: bool = False,
               fetcher: Fetcher | None = None) -> list[dict]:
    """Audit the vault for issues. Returns a list of issue dicts.

    ``refetch`` is opt-in and the *only* path on which lint touches the network:
    it re-fetches each URL-sourced raw and flags one whose upstream bytes no
    longer match the asset sha256 recorded at ingest (REQ-20). With ``refetch``
    off (the default), lint makes zero network calls. ``fetcher`` is injectable
    for tests; in normal use a real ``HttpFetcher`` is built only when needed."""
    if refetch and fetcher is None:
        fetcher = HttpFetcher()
    vault_config = load_vault_config(vault_path)
    topics = vault_config.get("topics", [])
    issues = []

    # Collect all pages and their titles
    all_pages = []
    all_titles = set()
    all_wikilinks = {}  # page_path -> set of link targets
    pages_with_incoming_links = set()

    for topic in topics:
        topic_dir = vault_path / topic
        if not topic_dir.is_dir():
            continue
        for md_file in topic_dir.rglob("*.md"):
            page = parse_page(md_file)
            rel = md_file.relative_to(vault_path)

            # Check missing frontmatter
            if not page["meta"]:
                issues.append({
                    "type": "missing_frontmatter",
                    "path": str(rel),
                    "detail": f"{rel} has no YAML frontmatter",
                })

            title = page["meta"].get("title", md_file.stem)
            all_titles.add(title)
            all_pages.append({"title": title, "path": rel})

            # raw <-> page body drift (read-only; direction-neutral report)
            for src in (page["meta"].get("sources") or []):
                if not src.startswith("raw/"):
                    continue
                raw_path = vault_path / src
                if not raw_path.is_file():
                    continue
                try:
                    raw_text = raw_path.read_text()
                except UnicodeDecodeError:
                    continue  # binary raw isn't text-comparable
                if page_raw_diverged(page["body"], raw_text):
                    n = page_lines_lost(page["body"], raw_text)
                    issues.append({
                        "type": "raw_page_drift",
                        "path": str(rel),
                        "detail": f"{rel} body differs from {src} ({n} line(s))",
                    })

            # stale-content: the page hasn't been updated since its newest source
            # moved on. "Newest source" is the max sidecar `ingested` across the
            # page's raw sources (binary sources count too — sidecar-only, so it
            # runs independent of the text-comparison above).
            updated = _as_date(page["meta"].get("updated"))
            source_dates = []
            for src in (page["meta"].get("sources") or []):
                if src.startswith("raw/"):
                    d = _as_date(load_sidecar(vault_path / src).get("ingested"))
                    if d is not None:
                        source_dates.append(d)
            if updated is not None and source_dates:
                newest = max(source_dates)
                behind = (newest - updated).days
                if behind > STALE_DAYS:
                    issues.append({
                        "type": "stale_content",
                        "path": str(rel),
                        "detail": f"{rel} updated {updated.isoformat()}, "
                                  f"{behind} days behind newest source ({newest.isoformat()})",
                    })

            # page-size: an over-long page body is a split candidate. Counts
            # content lines only (frontmatter excluded) via the canonical
            # raw-body normalizer.
            n_lines = len(page_body_for_raw(page["body"]).splitlines())
            if n_lines > PAGE_MAX_LINES:
                issues.append({
                    "type": "page_size",
                    "path": str(rel),
                    "detail": f"{rel} is {n_lines} lines (>{PAGE_MAX_LINES}); consider splitting",
                })

            links = extract_wikilinks(page["body"])
            all_wikilinks[str(rel)] = links

    # Check broken wikilinks
    for page_path, links in all_wikilinks.items():
        for link in links:
            if link in all_titles:
                pages_with_incoming_links.add(link)
            else:
                issues.append({
                    "type": "broken_wikilink",
                    "path": page_path,
                    "detail": f"[[{link}]] not found",
                })

    # Check orphan pages (no incoming links)
    for p in all_pages:
        if p["title"] not in pages_with_incoming_links:
            issues.append({
                "type": "orphan",
                "path": str(p["path"]),
                "detail": f"{p['title']} has no incoming wikilinks",
            })

    # Check un-ingested raw files
    raw_dir = vault_path / "raw"
    if raw_dir.is_dir():
        # Collect all sources referenced by pages
        referenced_raw = set()
        for topic in topics:
            topic_dir = vault_path / topic
            if not topic_dir.is_dir():
                continue
            for md_file in topic_dir.rglob("*.md"):
                page = parse_page(md_file)
                for src in page["meta"].get("sources", []):
                    referenced_raw.add(src)

        for raw_file in raw_dir.iterdir():
            if raw_file.is_file() and not is_sidecar(raw_file):
                raw_ref = f"raw/{raw_file.name}"
                sidecar = load_sidecar(raw_file)

                # source-drift: a raw edited in place no longer matches the sha256
                # its sidecar recorded at ingest. Unlike raw_page_drift (page vs
                # raw, direction-neutral), this pinpoints the raw as the changed
                # side. Pure byte-hash, so it covers binary raws too.
                recorded = sidecar.get("sha256")
                if recorded is not None and sha256_bytes(raw_file.read_bytes()) != recorded:
                    issues.append({
                        "type": "source_drift",
                        "path": raw_ref,
                        "detail": f"{raw_ref} edited in place — body no longer matches recorded sha256",
                    })

                # upstream-changed (opt-in): re-fetch a URL source and compare its
                # bytes to the asset sha256 recorded at ingest. A network hiccup
                # isn't an upstream change, so a FetchError is skipped, not flagged.
                src_url = sidecar.get("source")
                asset_hash = sidecar.get("asset_sha256")
                if refetch and asset_hash and src_url and is_url(src_url):
                    try:
                        fetched = fetcher.fetch(src_url)
                    except FetchError:
                        pass
                    else:
                        if sha256_bytes(fetched.body) != asset_hash:
                            issues.append({
                                "type": "upstream_changed",
                                "path": raw_ref,
                                "detail": f"{raw_ref} upstream source changed since ingest: {src_url}",
                            })

                if raw_ref not in referenced_raw:
                    issues.append({
                        "type": "raw_not_ingested",
                        "path": raw_ref,
                        "detail": f"{raw_file.name} in raw/ has no wiki page",
                    })

    return issues
