import os
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from agent_wiki.config import load_vault_config
from agent_wiki.page import (
    slugify, render_page, parse_page,
    page_raw_diverged, page_lines_lost, page_raw_diff,
    sha256_bytes, save_sidecar,
)
from agent_wiki.log import append_log


def _write_sidecar(raw_dest: Path, source: str, fetcher: str) -> None:
    """Write the provenance sidecar (raw/<name>.meta.yaml) for the just-stored raw
    file, hashing the stored body so sha256 always matches the raw on disk."""
    save_sidecar(raw_dest, {
        "source": source,
        "fetcher": fetcher,
        "ingested": datetime.now().isoformat(timespec="seconds"),
        "sha256": sha256_bytes(raw_dest.read_bytes()),
    })


class PageDriftError(ValueError):
    """A reingest/update would overwrite a page that has diverged from its raw,
    and --force was not given. `diff` is a unified page-vs-raw diff for display."""

    def __init__(self, message: str, diff: str = ""):
        super().__init__(message)
        self.diff = diff


def _extract_title(content: str, filename: str) -> str:
    """Extract title from first # heading, or fall back to filename stem."""
    match = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return Path(filename).stem


def resolve_raw(vault_path: Path, name: str) -> Path:
    """Resolve a user-given raw name to a file in raw/.

    Accepts 'foo', 'foo.md', or 'raw/foo.md'. Exact basename match wins; otherwise
    a unique stem match is used. Raises FileNotFoundError if nothing matches and
    ValueError if the name is ambiguous.
    """
    raw_dir = vault_path / "raw"
    if not raw_dir.is_dir():
        raise FileNotFoundError("vault has no raw/ directory")
    name = name.strip()
    if name.startswith("raw/"):
        name = name[len("raw/"):]
    name = Path(name).name  # drop any other path components
    direct = raw_dir / name
    if direct.is_file():
        return direct
    stem = Path(name).stem
    candidates = sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and (p.name == name or p.stem == stem)
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        joined = ", ".join(p.name for p in candidates)
        raise ValueError(f"ambiguous raw name {name!r}; matches: {joined}")
    raise FileNotFoundError(f"no raw file matching {name!r} in raw/")


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
    force: bool = False,
    provenance_source: str | None = None,
    provenance_fetcher: str = "local",
) -> Path:
    """Ingest a source file into the wiki vault.

    Copies file to raw/, creates a wiki page in the appropriate topic folder.
    With ``update=True``, overwrites an existing ``raw/<basename>`` and rewrites
    the page linked to it via its ``sources`` frontmatter. Without it, an
    existing ``raw/<basename>`` raises ``FileExistsError``.
    With ``force=True``, skips the drift guard and overwrites the page even if
    it has diverged from its raw source. Without force, raises ``PageDriftError``
    (with a ``.diff`` attribute containing a unified page-vs-raw diff) if the
    existing page body has diverged from its current raw/<name>; leaves both untouched.
    ``provenance_source``/``provenance_fetcher`` override what the sidecar records
    (default: the local source path and ``local``); URL ingest passes the URL and
    ``http`` so the sidecar reflects the true origin rather than the temp file.
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
            parsed_old = parse_page(old_path)
            old_meta = parsed_old["meta"] or {}
            # Data-loss guard: pages are generated from raw, so a page that differs
            # from its current raw is an anomaly. Refuse (with a diff) unless --force.
            if not force and raw_dest.is_file():
                try:
                    existing_raw = raw_dest.read_text()
                except UnicodeDecodeError:
                    existing_raw = None  # binary raw isn't text-comparable
                if existing_raw is not None and page_raw_diverged(parsed_old["body"], existing_raw):
                    page_rel = old_path.relative_to(vault_path)
                    lost = page_lines_lost(parsed_old["body"], existing_raw)
                    diff = page_raw_diff(parsed_old["body"], existing_raw,
                                         f"page:{page_rel}", f"raw:{raw_ref}")
                    raise PageDriftError(
                        f"page {page_rel} differs from {raw_ref}: {lost} page line(s) "
                        f"are not in the raw. awiki treats pages as generated from raw, "
                        f"so review before overwriting. Options: fold the page's changes "
                        f"into {raw_ref} and reingest; pass --force to discard them and "
                        f"rebuild from raw; or `awiki doctor --reconcile-raw` to adopt the "
                        f"page as raw.",
                        diff=diff,
                    )
            eff_topic = topic or old_meta.get("topic") or vault_config.get("default_topic", "research")
            new_path = vault_path / eff_topic / f"{slug}.md"
            if new_path.resolve() != old_path.resolve() and new_path.exists():
                raise ValueError(
                    f"cannot update: target page {new_path.relative_to(vault_path)} already exists"
                )

    # Skip the copy when source IS the destination (in-place reingest); else copy.
    if not (raw_dest.exists() and source.exists() and os.path.samefile(source, raw_dest)):
        shutil.copy2(source, raw_dest)

    # Every ingest writes a provenance sidecar next to the (now-final) raw body.
    _write_sidecar(
        raw_dest,
        source=provenance_source if provenance_source is not None else str(source),
        fetcher=provenance_fetcher,
    )

    if update and old_path is not None:
        # Carry arbitrary extra frontmatter keys (e.g. source_url) through the
        # reingest, then rebuild the managed keys on top — don't drop what we
        # don't manage.
        meta = dict(old_meta)
        meta.update({
            "title": title,
            "topic": eff_topic,
            "tags": tags if tags is not None else (old_meta.get("tags") or []),
            "created": old_meta.get("created", today),
            "updated": today,
            "sources": _merge_sources(old_meta.get("sources"), raw_ref),
        })
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


def url_to_name(url: str) -> str:
    """A filesystem-safe stem for a fetched URL's raw file and page, derived from
    the URL (last path segment, else host). Minimal v1; T-07 layers on full URL
    normalization and a dedup match key."""
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    base = segments[-1] if segments else parsed.netloc
    base = re.sub(r"\.(html?|php|aspx?)$", "", base, flags=re.IGNORECASE)
    return slugify(base) or slugify(parsed.netloc) or "page"


def ingest_url(
    url: str,
    vault_path: Path,
    topic: str | None = None,
    tags: list[str] | None = None,
    fetcher=None,
    update: bool = False,
    force: bool = False,
) -> Path:
    """Fetch a URL, extract clean main-content markdown, and ingest it as
    ``raw/<name>.md`` with an http-provenance sidecar.

    Network access happens only through ``fetcher`` (defaults to the built-in
    ``HttpFetcher``); extraction runs on the already-fetched bytes. The extracted
    markdown is staged as a temp file and handed to ``ingest_file`` so URL ingest
    reuses the page/sidecar/log machinery, overriding only the recorded provenance.
    Returns the path to the created/updated wiki page.
    """
    from agent_wiki.fetch import HttpFetcher, extract

    fetcher = fetcher or HttpFetcher()
    result = fetcher.fetch(url)
    extracted = extract(result.body, result.content_type)
    name = url_to_name(result.source_url)

    with tempfile.TemporaryDirectory() as td:
        staged = Path(td) / f"{name}.md"
        staged.write_text(extracted.markdown)
        return ingest_file(
            staged, vault_path, topic=topic, tags=tags, update=update, force=force,
            provenance_source=result.source_url, provenance_fetcher="http",
        )
