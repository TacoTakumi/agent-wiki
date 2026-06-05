import re
import shutil
from datetime import date
from pathlib import Path
import yaml

from agent_wiki.config import load_vault_config
from agent_wiki.page import slugify, render_page, parse_page
from agent_wiki.log import append_log


def _extract_title(content: str, filename: str) -> str:
    """Extract title from first # heading, or fall back to filename stem."""
    match = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return Path(filename).stem


def _find_pages_by_source(vault_path: Path, raw_ref: str, topics: list[str]) -> list[Path]:
    """Return page files whose frontmatter `sources` lists `raw_ref`."""
    matches: list[Path] = []
    for topic in topics:
        topic_dir = vault_path / topic
        if not topic_dir.is_dir():
            continue
        for md_file in topic_dir.rglob("*.md"):
            page = parse_page(md_file)
            if raw_ref in (page["meta"].get("sources") or []):
                matches.append(md_file)
    return matches


def _merge_sources(existing: list | None, raw_ref: str) -> list[str]:
    out = list(existing or [])
    if raw_ref not in out:
        out.append(raw_ref)
    return out


def ingest_file(
    source: Path,
    vault_path: Path,
    topic: str | None = None,
    tags: list[str] | None = None,
    update: bool = False,
) -> Path:
    """Ingest a source file into the wiki vault.

    Copies file to raw/, creates a wiki page in the appropriate topic folder.
    With ``update=True``, overwrites an existing ``raw/<basename>`` and rewrites
    the page linked to it via its ``sources`` frontmatter. Without it, an
    existing ``raw/<basename>`` raises ``FileExistsError``.
    Returns the path to the created/updated wiki page.
    """
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    vault_config = load_vault_config(vault_path)
    raw_ref = f"raw/{source.name}"
    raw_dest = vault_path / "raw" / source.name

    if raw_dest.exists() and not update:
        raise FileExistsError(
            f"{raw_ref} already exists; pass --update to overwrite and update the linked page"
        )

    content = source.read_text()
    title = _extract_title(content, source.name)
    slug = slugify(title)
    today = date.today().isoformat()

    # On update, resolve the target page and run ALL pre-flight checks BEFORE
    # mutating raw, so any failure leaves the vault untouched.
    old_path: Path | None = None
    old_meta: dict = {}
    new_path: Path | None = None
    eff_topic = topic or vault_config.get("default_topic", "research")
    if update:
        existing = _find_pages_by_source(vault_path, raw_ref, vault_config.get("topics", []))
        if len(existing) > 1:
            joined = ", ".join(str(p.relative_to(vault_path)) for p in existing)
            raise ValueError(f"multiple pages link {raw_ref}; reconcile manually: {joined}")
        if existing:
            old_path = existing[0]
            old_meta = parse_page(old_path)["meta"] or {}
            eff_topic = topic or old_meta.get("topic") or vault_config.get("default_topic", "research")
            new_path = vault_path / eff_topic / f"{slug}.md"
            if new_path.resolve() != old_path.resolve() and new_path.exists():
                raise ValueError(
                    f"cannot update: target page {new_path.relative_to(vault_path)} already exists"
                )

    shutil.copy2(source, raw_dest)

    if update and old_path is not None:
        meta = {
            "title": title,
            "topic": eff_topic,
            "tags": tags if tags is not None else (old_meta.get("tags") or []),
            "created": old_meta.get("created", today),
            "updated": today,
            "sources": _merge_sources(old_meta.get("sources"), raw_ref),
        }
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(render_page(meta, content))
        if new_path.resolve() != old_path.resolve():
            old_path.unlink()
        append_log(vault_path, "update", f"{source.name} -> {eff_topic}/{slug}.md")
        return new_path

    # Fresh page: a normal ingest, or an update whose raw had no linked page.
    meta = {
        "title": title,
        "topic": eff_topic,
        "tags": tags or [],
        "created": today,
        "updated": today,
        "sources": [raw_ref],
    }
    page_path = vault_path / eff_topic / f"{slug}.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(render_page(meta, content))
    append_log(vault_path, "update" if update else "ingest",
               f"{source.name} -> {eff_topic}/{slug}.md")
    return page_path
