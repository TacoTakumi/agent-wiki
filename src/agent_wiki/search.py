import re
import subprocess
import shutil
from pathlib import Path

from agent_wiki.page import parse_page


def _search_ripgrep(vault_path: Path, query: str, topic: str | None) -> list[dict]:
    """Search using ripgrep for speed."""
    search_path = vault_path / topic if topic else vault_path
    cmd = [
        "rg", "--iglob", "*.md",
        "--ignore-case",
        "--line-number",
        "--no-heading",
        query,
        str(search_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode not in (0, 1):
        return []

    # Group matches by file
    file_matches: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        # ripgrep output: path:line_number:content
        parts = line.split(":", 2)
        if len(parts) >= 3:
            filepath = parts[0]
            content = parts[2].strip()
            file_matches.setdefault(filepath, []).append(content)

    results = []
    for filepath, matches in file_matches.items():
        path = Path(filepath)
        # Skip raw/, index.md, log.md
        try:
            rel = path.relative_to(vault_path)
        except ValueError:
            continue
        if str(rel).startswith("raw/") or rel.name in ("index.md", "log.md"):
            continue

        page = parse_page(path)
        title = page["meta"].get("title", path.stem)
        results.append({
            "title": title,
            "path": str(rel),
            "matches": matches,
        })

    return results


def _search_python(vault_path: Path, query: str, topic: str | None) -> list[dict]:
    """Fallback search using Python regex."""
    search_path = vault_path / topic if topic else vault_path
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []

    for md_file in search_path.rglob("*.md"):
        rel = md_file.relative_to(vault_path)
        if str(rel).startswith("raw/") or rel.name in ("index.md", "log.md"):
            continue

        content = md_file.read_text()
        matching_lines = [
            line.strip()
            for line in content.splitlines()
            if pattern.search(line)
        ]

        if matching_lines:
            page = parse_page(md_file)
            title = page["meta"].get("title", md_file.stem)
            results.append({
                "title": title,
                "path": str(rel),
                "matches": matching_lines,
            })

    return results


def search_vault(
    vault_path: Path, query: str, topic: str | None = None
) -> list[dict]:
    """Search the wiki vault. Uses ripgrep if available, falls back to Python."""
    if shutil.which("rg"):
        return _search_ripgrep(vault_path, query, topic)
    return _search_python(vault_path, query, topic)
