import re
from pathlib import Path
import yaml


def slugify(text: str) -> str:
    """Convert text to a URL/filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def parse_page(path: Path) -> dict:
    """Parse a wiki page into meta (frontmatter) and body."""
    content = path.read_text()

    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            return {"meta": meta, "body": body, "path": path}

    return {"meta": {}, "body": content, "path": path}


def render_page(meta: dict, body: str) -> str:
    """Render a wiki page with YAML frontmatter and body."""
    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False)
    return f"---\n{frontmatter}---\n\n{body}"


def extract_wikilinks(text: str) -> set[str]:
    """Extract all [[wikilink]] targets from text."""
    return set(re.findall(r"\[\[([^\]]+)\]\]", text))
