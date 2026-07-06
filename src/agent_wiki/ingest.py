import os
import re
import shutil
import tempfile
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import click

from agent_wiki.config import load_vault_config, parse_tag_vocabulary, TAG_MODES
from agent_wiki.tags import canonicalize_tags
from agent_wiki.page import (
    slugify, render_page, parse_page, update_frontmatter, render_hash,
    page_raw_diverged, page_lines_lost, page_raw_diff,
    sha256_bytes, load_sidecar, save_sidecar,
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


def _stamp_render_hash(page_path: Path) -> None:
    """Recompute render_hash from the page's own on-disk body and write it into the
    page's frontmatter, leaving the body byte-identical (via update_frontmatter).

    Called immediately after each ingest_file page write, so the stamp is always
    the hash of exactly the body that landed on disk — recomputed, never carried
    stale from a prior render (REQ-01) — and hashed from the *parsed* body so it
    equals the value the drift guard recomputes on a clean page (REQ-05)."""
    parsed = parse_page(page_path)
    meta = parsed["meta"]
    meta["render_hash"] = render_hash(parsed["body"])
    update_frontmatter(page_path, meta)


class StrictTagError(ValueError):
    """Strict tag mode encountered a novel out-of-vocabulary tag. Raised pre-flight
    (before any raw/sidecar/page write) so the vault is left byte-unchanged.
    `novel` is the offending tag list."""

    def __init__(self, novel: list[str]):
        self.novel = list(novel)
        listed = ", ".join(repr(t) for t in self.novel)
        super().__init__(
            f"strict tag mode: {listed} not in the vocabulary; nothing was written"
        )


def _resolve_tags(tags: list[str], vocab) -> list[str]:
    """Canonicalize a page's tags pre-flight and return the canonical list.

    In strict mode a novel tag raises StrictTagError BEFORE the caller mutates the
    vault, so the ingest aborts cleanly (REQ-06). Otherwise each alias remap is
    announced and each novel tag warned (warn mode). With an off/empty vocabulary
    this is inert: canonicalize_tags returns the tags untouched with no remaps or
    novel tags, so nothing prints and a no-`tags:`-block vault is unaffected."""
    result = canonicalize_tags(tags, vocab)
    if vocab.mode == "strict" and result.novel:
        raise StrictTagError(result.novel)
    for original, preferred in result.remaps:
        click.echo(f"tag '{original}' canonicalized to '{preferred}'")
    for tag in result.novel:
        click.echo(f"warning: novel tag '{tag}' is not in the vocabulary (kept)")
    return result.tags


class UnchangedURLSkip(Exception):
    """A re-ingest of a URL whose freshly-extracted body matches the stored
    sidecar sha256 — nothing was rewritten. Not an error: callers report it and
    exit 0. ``url`` is the normalized URL; ``page`` is the existing page if known."""

    def __init__(self, url: str, page: Path | None = None):
        super().__init__(f"unchanged since last ingest: {url}")
        self.url = url
        self.page = page


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


def _first_h1(markdown: str) -> str | None:
    """The first markdown H1 heading text, or None."""
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else None


def _resolve_title(extractor_title: str | None, markdown: str, slug: str) -> str:
    """Fetched-page title precedence (REQ-15): extractor title metadata, then the
    first markdown H1, then the URL-derived slug."""
    if extractor_title and extractor_title.strip():
        return extractor_title.strip()
    return _first_h1(markdown) or slug


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
    extra_frontmatter: dict | None = None,
    slug_override: str | None = None,
    title_override: str | None = None,
    tag_mode: str | None = None,
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
    ``extra_frontmatter`` is merged onto the page's frontmatter (e.g. inline
    ``source_url`` for a fetched page); operational provenance never goes here.
    ``slug_override`` forces the page filename stem (URL ingest passes a
    URL-derived stem so a re-fetch with a changed title maps to the same page).
    ``title_override`` forces the page title (URL ingest resolves it by precedence
    before calling); otherwise the title comes from the first heading or filename.
    ``tag_mode`` forces the vocabulary mode (off|warn|strict) for this one ingest,
    overriding the vault's configured mode without mutating it (REQ-14).
    Returns the path to the created/updated wiki page.
    """
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    vault_config = load_vault_config(vault_path)
    vocab = parse_tag_vocabulary(vault_config)
    if tag_mode is not None:
        if tag_mode not in TAG_MODES:
            raise ValueError(
                f"invalid tag mode {tag_mode!r}; expected one of {', '.join(TAG_MODES)}"
            )
        vocab = replace(vocab, mode=tag_mode)
    raw_ref = f"raw/{source.name}"
    raw_dest = vault_path / "raw" / source.name

    if raw_dest.exists() and not update:
        raise FileExistsError(
            f"{raw_ref} already exists; pass --update to overwrite and update the linked page"
        )

    content = source.read_text()
    title = title_override or _extract_title(content, source.name)
    slug = slug_override or slugify(title)
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
            # Data-loss guard: a page is generated from its raw, so an out-of-band
            # page hand-edit (body changed without a reingest) is the anomaly we
            # refuse to overwrite (with a diff) unless --force.
            #
            # For a page carrying a render_hash, "hand-edited" is decided against
            # the stamp, not the raw: the guard fires iff the current page body no
            # longer hashes to its stored render_hash. Editing only the raw leaves
            # the page body — and its hash — untouched, so a reingest rebuilds
            # cleanly (REQ-03). Pages without a stamp (legacy/foreign) fall back to
            # the page-vs-raw comparison; T-04 refines that to lazy TOFU.
            if not force and raw_dest.is_file():
                try:
                    existing_raw = raw_dest.read_text()
                except UnicodeDecodeError:
                    existing_raw = None  # binary raw isn't text-comparable
                stored_hash = old_meta.get("render_hash")
                if stored_hash is not None:
                    drifted = render_hash(parsed_old["body"]) != stored_hash
                else:
                    drifted = (existing_raw is not None
                               and page_raw_diverged(parsed_old["body"], existing_raw))
                if drifted:
                    page_rel = old_path.relative_to(vault_path)
                    lost = page_lines_lost(parsed_old["body"], existing_raw or "")
                    diff = page_raw_diff(parsed_old["body"], existing_raw or "",
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

    # Resolve + canonicalize the effective tags BEFORE any mutation, so a strict
    # rejection of a novel tag aborts with no raw/sidecar/page written (REQ-06).
    if update and old_path is not None:
        eff_tags = tags if tags is not None else (old_meta.get("tags") or [])
    else:
        eff_tags = tags or []
    canonical_tags = _resolve_tags(eff_tags, vocab)

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
            "tags": canonical_tags,
            "created": old_meta.get("created", today),
            "updated": today,
            "sources": _merge_sources(old_meta.get("sources"), raw_ref),
        })
        if extra_frontmatter:
            meta.update(extra_frontmatter)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(render_page(meta, content))
        _stamp_render_hash(new_path)
        if new_path.resolve() != old_path.resolve():
            old_path.unlink()
        append_log(vault_path, "update", f"{source.name} -> {eff_topic}/{slug}.md")
        return new_path

    # Fresh page: a normal ingest, or an update whose raw had no linked page.
    meta = {
        "title": title,
        "topic": eff_topic,
        "tags": canonical_tags,
        "created": today,
        "updated": today,
        "sources": [raw_ref],
    }
    if extra_frontmatter:
        meta.update(extra_frontmatter)
    page_path = vault_path / eff_topic / f"{slug}.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(render_page(meta, content))
    _stamp_render_hash(page_path)
    append_log(vault_path, "update" if update else "ingest",
               f"{source.name} -> {eff_topic}/{slug}.md")
    return page_path


# Content-type -> archived-asset extension. Unmapped text types get a sane
# default (REQ-10 / asset-extension open question); PDF arrives with T-10.
_ASSET_EXT = {"text/html": ".html", "application/pdf": ".pdf"}


def _asset_ext(content_type: str) -> str:
    return _ASSET_EXT.get(content_type, ".txt")


def _archive_asset(vault_path: Path, name: str, body: bytes,
                   content_type: str, raw_dest: Path) -> None:
    """Archive the original fetched artifact byte-identically under raw/assets/
    and record its own sha256 (and path) in the raw body's sidecar, alongside the
    raw-body sha256."""
    data = body if isinstance(body, bytes) else body.encode()
    assets_dir = vault_path / "raw" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    asset_rel = f"raw/assets/{name}{_asset_ext(content_type)}"
    (vault_path / asset_rel).write_bytes(data)

    meta = load_sidecar(raw_dest)
    meta["asset"] = asset_rel
    meta["asset_sha256"] = sha256_bytes(data)
    save_sidecar(raw_dest, meta)


def normalize_url(url: str) -> str:
    """Canonicalize a URL for naming + dedup (REQ-14): lowercase scheme and host,
    strip the default port, drop the fragment, and trim a single trailing slash.
    The query string is kept verbatim for v1."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = f"{host}:{port}" if (port and not default) else host
    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def url_to_name(url: str) -> str:
    """A filesystem-safe stem for a fetched URL's raw file and page, derived from
    the normalized URL (last path segment, else host) — never from the page title,
    so it is stable across title changes."""
    parsed = urlparse(normalize_url(url))
    segments = [s for s in parsed.path.split("/") if s]
    base = segments[-1] if segments else parsed.netloc
    base = re.sub(r"\.(html?|php|aspx?|pdf)$", "", base, flags=re.IGNORECASE)
    return slugify(base) or slugify(parsed.netloc) or "page"


def fetch_and_extract(url: str, fetcher=None, pdf_extractor: str = "pymupdf4llm"):
    """Client-side half of URL ingest (D-17/REQ-09): reach the network through a
    ``Fetcher`` and extract clean main-content markdown from the fetched bytes.

    Touches no vault — its outputs (the canonical/redirected URL, content type,
    original asset bytes, extracted markdown, and extractor title) are exactly what
    a remote client ships to the server for ``ingest_extracted``, so fetch+extract
    stays client-side and the server performs no outbound request. Network access
    happens only through ``fetcher`` (defaults to the built-in ``HttpFetcher``).
    Returns ``(FetchResult, ExtractResult)``.
    """
    from agent_wiki.fetch import HttpFetcher, extract

    fetcher = fetcher or HttpFetcher()
    result = fetcher.fetch(url)
    extracted = extract(result.body, result.content_type, pdf_extractor=pdf_extractor)
    return result, extracted


def ingest_extracted(
    vault_path: Path,
    source_url: str,
    content_type: str,
    asset: bytes,
    markdown: str,
    extractor_title: str | None = None,
    topic: str | None = None,
    tags: list[str] | None = None,
    update: bool = False,
    force: bool = False,
    tag_mode: str | None = None,
) -> Path:
    """Server-side half of URL ingest: ingest already-fetched, already-extracted
    content into ``raw/<name>.md`` with an http-provenance sidecar.

    Makes NO network call and constructs no ``Fetcher`` — this is the seam the
    awiki server runs so it performs no outbound fetch (D-17/REQ-09). ``markdown``
    is the extracted body; ``asset``/``content_type`` are the original fetched
    artifact, archived byte-identically under ``raw/assets/``. Dedup keys on the
    normalized ``source_url``; an unchanged body (matching the stored sidecar
    sha256) raises ``UnchangedURLSkip`` unless ``force``. The extracted markdown is
    staged as a temp file and handed to ``ingest_file`` so URL ingest reuses the
    page/sidecar/log machinery, overriding only the recorded provenance.
    Returns the path to the created/updated wiki page.
    """
    canonical = normalize_url(source_url)
    name = url_to_name(canonical)
    raw_dest = vault_path / "raw" / f"{name}.md"

    # Dedup: re-fetching the same URL updates its page in place rather than
    # orphaning a title-named duplicate. The match key is the normalized URL; an
    # unchanged body (matching the stored sidecar sha256) is a no-op skip unless
    # --force.
    if raw_dest.exists():
        prior = load_sidecar(raw_dest)
        prior_source = prior.get("source")
        if prior_source is not None and normalize_url(prior_source) == canonical:
            if not force and prior.get("sha256") == sha256_bytes(markdown.encode()):
                pages = _find_pages_by_source(
                    vault_path, f"raw/{name}.md",
                    load_vault_config(vault_path).get("topics", []))
                raise UnchangedURLSkip(canonical, pages[0] if pages else None)
            update = True  # same URL, changed body (or forced) -> update in place

    title = _resolve_title(extractor_title, markdown, name)

    with tempfile.TemporaryDirectory() as td:
        staged = Path(td) / f"{name}.md"
        staged.write_text(markdown)
        page_path = ingest_file(
            staged, vault_path, topic=topic, tags=tags, update=update, force=force,
            provenance_source=source_url, provenance_fetcher="http",
            extra_frontmatter={"source_url": source_url},
            slug_override=name, title_override=title, tag_mode=tag_mode,
        )

    # Archive the original fetched artifact next to the raw body and record its
    # own sha256 in the sidecar.
    raw_dest = vault_path / "raw" / f"{name}.md"
    _archive_asset(vault_path, name, asset, content_type, raw_dest)
    return page_path


def ingest_url(
    url: str,
    vault_path: Path,
    topic: str | None = None,
    tags: list[str] | None = None,
    fetcher=None,
    update: bool = False,
    force: bool = False,
    tag_mode: str | None = None,
) -> Path:
    """Fetch a URL, extract clean main-content markdown, and ingest it as
    ``raw/<name>.md`` with an http-provenance sidecar.

    The whole-pipeline convenience used for a local vault (fetch + extract +
    ingest in one process). Remote clients instead call ``fetch_and_extract``
    client-side and ship the result to the server's ``ingest_extracted`` so the
    server never fetches (D-17/REQ-09). Returns the created/updated wiki page.
    """
    pdf_extractor = load_vault_config(vault_path).get("pdf_extractor", "pymupdf4llm")
    result, extracted = fetch_and_extract(url, fetcher=fetcher, pdf_extractor=pdf_extractor)
    return ingest_extracted(
        vault_path,
        source_url=result.source_url,
        content_type=result.content_type,
        asset=result.body,
        markdown=extracted.markdown,
        extractor_title=extracted.title,
        topic=topic, tags=tags, update=update, force=force, tag_mode=tag_mode,
    )
