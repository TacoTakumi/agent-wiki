import subprocess
import shutil
from pathlib import Path

from agent_wiki.page import parse_page

# Files/dirs that are never search results.
_SKIP_NAMES = ("index.md", "log.md")


def _tokenize(query: str) -> list[str]:
    """Split a query into lowercased, whitespace-separated tokens.

    Single source of truth for both backends. Order is preserved and
    duplicates are dropped (first occurrence wins). Empty/whitespace → [].
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in query.split():
        tok = raw.lower()
        if tok and tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    return tokens


def _skip(rel: Path) -> bool:
    """True for paths that must never appear in results (raw/, index, log)."""
    return str(rel).startswith("raw/") or rel.name in _SKIP_NAMES


def _search_ripgrep(
    vault_path: Path, tokens: list[str], topic: str | None
) -> dict[str, list[str]]:
    """Collect matched lines via ripgrep. Returns {abs_path: [lines]}.

    Each token is passed as a literal `-e` pattern with --fixed-strings, so
    ripgrep matches lines containing ANY token (OR of literals). No regex
    escaping is involved.
    """
    search_path = vault_path / topic if topic else vault_path
    cmd = [
        "rg", "--iglob", "*.md",
        "--fixed-strings",
        "--ignore-case",
        "--null",
        "--no-heading",
    ]
    for tok in tokens:
        cmd += ["-e", tok]
    cmd.append(str(search_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    if result.returncode not in (0, 1):
        return {}

    file_matches: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        # --null makes ripgrep emit "<path>\0<content>", so the path is
        # delimited cleanly even when the path or content contains colons.
        path_str, sep, content = line.partition("\0")
        if sep:
            file_matches.setdefault(path_str, []).append(content.strip())
    return file_matches


def _search_python(
    vault_path: Path, tokens: list[str], topic: str | None
) -> dict[str, list[str]]:
    """Fallback collector: substring scan. Returns {abs_path: [lines]}.

    A line matches if it contains ANY token (case-insensitive substring),
    mirroring the ripgrep OR-of-literals semantics.
    """
    search_path = vault_path / topic if topic else vault_path
    file_matches: dict[str, list[str]] = {}

    for md_file in search_path.rglob("*.md"):
        rel = md_file.relative_to(vault_path)
        if _skip(rel):
            continue

        content = md_file.read_text()
        matching = [
            line.strip()
            for line in content.splitlines()
            if any(tok in line.lower() for tok in tokens)
        ]
        if matching:
            file_matches[str(md_file)] = matching

    return file_matches


def _rank(
    vault_path: Path, file_matches: dict[str, list[str]], tokens: list[str]
) -> list[dict]:
    """Turn {path: [lines]} into ranked result dicts.

    Per file, coverage = number of distinct tokens present in its matched
    lines. match_kind is "all" when every token is present, else "partial".
    Sorted by (coverage desc, match-count desc, title asc). Coverage uses
    case-insensitive substring tests, so a token like `log` counts as
    present in `login`.
    """
    n = len(tokens)
    results = []

    for filepath, matches in file_matches.items():
        path = Path(filepath)
        # Skip raw/, index.md, log.md (rg path needs this; Python path already filtered).
        try:
            rel = path.relative_to(vault_path)
        except ValueError:
            continue
        if _skip(rel):
            continue

        haystack = "\n".join(matches).lower()
        coverage = sum(1 for tok in tokens if tok in haystack)
        if coverage == 0:
            continue

        page = parse_page(path)
        title = page["meta"].get("title", path.stem)
        results.append({
            "title": title,
            "path": str(rel),
            "matches": matches,
            "coverage": coverage,
            "term_count": n,
            "match_kind": "all" if coverage == n else "partial",
        })

    results.sort(key=lambda r: (-r["coverage"], -len(r["matches"]), r["title"]))
    return results


def search_vault(
    vault_path: Path, query: str, topic: str | None = None
) -> list[dict]:
    """Search the wiki vault. Returns ALL ranked results; callers cap.

    Multi-word queries are AND-across-the-page: every result reports a
    `coverage` (distinct tokens matched) and a `match_kind` of "all" or
    "partial". Uses ripgrep if available, falls back to a Python scan.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    if shutil.which("rg"):
        file_matches = _search_ripgrep(vault_path, tokens, topic)
    else:
        file_matches = _search_python(vault_path, tokens, topic)

    return _rank(vault_path, file_matches, tokens)
