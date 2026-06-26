import difflib
import hashlib
import re
from pathlib import Path
import yaml


SIDECAR_SUFFIX = ".meta.yaml"


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 digest of raw bytes — the canonical hash for raw bodies/assets."""
    return hashlib.sha256(data).hexdigest()


def sidecar_path(raw_path: Path) -> Path:
    """Provenance sidecar path for a raw file: raw/<name>.md -> raw/<name>.meta.yaml."""
    return raw_path.with_suffix(SIDECAR_SUFFIX)


def is_sidecar(path: Path) -> bool:
    """True if path is a provenance sidecar (raw/<name>.meta.yaml) rather than a raw
    source. The single predicate for telling sidecars apart from raw bodies, so raw
    enumeration/counting never mistakes the metadata for a source."""
    return path.name.endswith(SIDECAR_SUFFIX)


def load_sidecar(raw_path: Path) -> dict:
    """Read a raw file's provenance sidecar via the canonical YAML reader.

    Returns {} when no sidecar exists. This is the single sanctioned reader of
    *.meta.yaml — no other module parses sidecars by hand.
    """
    sc = sidecar_path(raw_path)
    if not sc.is_file():
        return {}
    return yaml.safe_load(sc.read_text()) or {}


def save_sidecar(raw_path: Path, meta: dict) -> Path:
    """Write a raw file's provenance sidecar via the canonical YAML dumper.

    Returns the sidecar path. This is the single sanctioned writer of *.meta.yaml.
    """
    sc = sidecar_path(raw_path)
    sc.write_text(yaml.dump(meta, default_flow_style=False, sort_keys=False))
    return sc


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


def page_body_for_raw(body: str) -> str:
    """Page body as it should appear in raw/: drop the single leading blank line
    that render_page inserts, and normalize to one trailing newline."""
    if body.startswith("\n"):
        body = body[1:]
    return body.rstrip("\n") + "\n"


def page_raw_diverged(page_body: str, raw_text: str) -> bool:
    """True if a page's body differs from its raw source (beyond normalization)."""
    return page_body_for_raw(page_body) != raw_text.rstrip("\n") + "\n"


def page_lines_lost(page_body: str, raw_text: str) -> int:
    """Count current-page lines that diverge from the raw — the page content a
    rebuild-from-raw would overwrite."""
    page_lines = page_body_for_raw(page_body).splitlines()
    raw_lines = (raw_text.rstrip("\n") + "\n").splitlines()
    return sum(1 for line in difflib.ndiff(page_lines, raw_lines)
               if line.startswith("- "))


def page_raw_diff(page_body: str, raw_text: str,
                  page_label: str, raw_label: str) -> str:
    """Unified diff of page (fromfile) vs raw (tofile): '-' lines are page content
    that a rebuild would lose, '+' lines are raw content that would replace it."""
    page_lines = page_body_for_raw(page_body).splitlines(keepends=True)
    raw_lines = (raw_text.rstrip("\n") + "\n").splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        page_lines, raw_lines, fromfile=page_label, tofile=raw_label))
