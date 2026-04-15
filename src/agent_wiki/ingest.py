import re
import shutil
from datetime import date
from pathlib import Path
import yaml

from agent_wiki.config import load_vault_config
from agent_wiki.page import slugify, render_page
from agent_wiki.log import append_log


def _extract_title(content: str, filename: str) -> str:
    """Extract title from first # heading, or fall back to filename stem."""
    match = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return Path(filename).stem


def ingest_file(
    source: Path,
    vault_path: Path,
    topic: str | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Ingest a source file into the wiki vault.

    Copies file to raw/, creates a wiki page in the appropriate topic folder.
    Returns the path to the created wiki page.
    """
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    vault_config = load_vault_config(vault_path)

    if topic is None:
        topic = vault_config.get("default_topic", "research")

    # Copy to raw/
    raw_dest = vault_path / "raw" / source.name
    shutil.copy2(source, raw_dest)

    # Read content and extract title
    content = source.read_text()
    title = _extract_title(content, source.name)

    # Build wiki page
    today = date.today().isoformat()
    meta = {
        "title": title,
        "topic": topic,
        "tags": tags or [],
        "created": today,
        "updated": today,
        "sources": [f"raw/{source.name}"],
    }

    page_content = render_page(meta, content)

    # Write to topic folder
    slug = slugify(title)
    page_path = vault_path / topic / f"{slug}.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(page_content)

    # Log the action
    append_log(vault_path, "ingest", f"{source.name} -> {topic}/{slug}.md")

    return page_path
