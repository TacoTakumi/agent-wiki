from pathlib import Path
from agent_wiki.config import load_vault_config
from agent_wiki.page import parse_page, extract_wikilinks


def lint_vault(vault_path: Path) -> list[dict]:
    """Audit the vault for issues. Returns a list of issue dicts."""
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
            if raw_file.is_file():
                raw_ref = f"raw/{raw_file.name}"
                if raw_ref not in referenced_raw:
                    issues.append({
                        "type": "raw_not_ingested",
                        "path": raw_ref,
                        "detail": f"{raw_file.name} in raw/ has no wiki page",
                    })

    return issues
