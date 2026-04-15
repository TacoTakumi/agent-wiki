from pathlib import Path
from agent_wiki.config import load_vault_config
from agent_wiki.page import parse_page


def rebuild_index(vault_path: Path) -> None:
    """Rebuild index.md from all wiki pages, grouped by topic."""
    vault_config = load_vault_config(vault_path)
    topics = vault_config.get("topics", [])

    lines = ["# Index\n"]

    for topic in topics:
        topic_dir = vault_path / topic
        if not topic_dir.is_dir():
            continue

        pages = []
        for md_file in sorted(topic_dir.rglob("*.md")):
            page = parse_page(md_file)
            meta = page["meta"]
            if not meta:
                continue
            pages.append({
                "title": meta.get("title", md_file.stem),
                "path": md_file.relative_to(vault_path),
                "tags": meta.get("tags", []),
                "updated": meta.get("updated", ""),
            })

        if not pages:
            continue

        lines.append(f"\n## {topic.capitalize()}\n")
        for p in pages:
            tags_str = f" `{', '.join(p['tags'])}`" if p["tags"] else ""
            lines.append(
                f"- [[{p['title']}]] ({p['path']}){tags_str} — {p['updated']}"
            )

    lines.append("")
    (vault_path / "index.md").write_text("\n".join(lines))
