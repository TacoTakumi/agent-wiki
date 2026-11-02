"""File a converted local document (PDF, HTML, EPUB) into the vault.

A document with one chapter becomes a single page through ``ingest_extracted``
(raw/<doc>.md, raw/assets/<doc>.<ext>, <topic>/<doc>.md). From two chapters up
it is laid out as:

- raw/assets/<doc>.<ext>            the original, byte-identical
- raw/assets/<doc>/<member path>    each image a chapter references, and the cover
- raw/<doc>/NN-<chapter-slug>.md    one raw per chapter, with its sidecar
- <topic>/<doc>.md                  the parent: metadata, cover line, chapter links
- <topic>/<doc>/NN-<chapter-slug>.md one page per chapter

where <doc> is the slug of the document title ('<slug>-document' when the
slug names a reserved raw/ folder, sessions or assets). A chapter raw holds
exactly the chapter page body, image references included (written relative to
the page), so rebuilding a chapter page from its raw is a plain render.

Ingesting a document whose files are already in the vault needs ``update``: the
new conversion replaces the whole set, and files the old set owned that the new
one no longer produces are deleted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

from agent_wiki.config import load_vault_config
from agent_wiki.convert.model import ConvertedDocument
from agent_wiki.convert.split import format_ordinal
from agent_wiki.ingest import (
    _RAW_NON_SOURCE_DIRS, PageDriftError, _load_vocab, _resolve_tags, _write_sidecar,
    ingest_extracted,
)
from agent_wiki.log import append_log
from agent_wiki.page import (
    is_sidecar, load_sidecar, page_lines_lost, page_raw_diff, page_raw_diverged,
    parse_page, render_hash, render_page, sidecar_path, slugify, stamp_render_hash,
)

DOCUMENT_SUFFIXES = frozenset({".pdf", ".html", ".htm", ".epub"})
# The image types extracted under raw/assets/<doc>/: the types the viewer serves.
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"})


class DocumentIngest(NamedTuple):
    """The page to report (the parent, or the single page) and the chapter count."""

    page: Path
    chapters: int


def convert_document(source: Path, pdf_extractor: str = "pymupdf4llm") -> ConvertedDocument:
    """Convert a local file by its extension. Raises ValueError when the file is
    not a supported format or its content does not match the extension."""
    suffix = source.suffix.lower()
    if suffix == ".epub":
        from agent_wiki.convert.epub import convert_epub
        return convert_epub(source)
    if suffix in (".html", ".htm"):
        from agent_wiki.convert.html import convert_html
        return convert_html(source)
    if suffix == ".pdf":
        from agent_wiki.convert.pdf import convert_pdf
        return convert_pdf(source, extractor=pdf_extractor)
    raise ValueError(f"not a convertible document: {source.name}")


def ingest_document(source: Path, vault_path: Path, topic: str | None = None,
                    tags: list[str] | None = None, tag_mode: str | None = None,
                    update: bool = False, force: bool = False,
                    provenance_source: str | None = None) -> DocumentIngest:
    """Convert ``source`` and write it into the vault (see ``write_document``).
    The PDF extractor is the vault's ``pdf_extractor`` setting.
    ``provenance_source`` is the path the sidecars record instead of ``source``."""
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")
    pdf_extractor = load_vault_config(vault_path).get("pdf_extractor", "pymupdf4llm")
    doc = convert_document(source, pdf_extractor=pdf_extractor)
    return write_document(doc, source, vault_path, topic=topic, tags=tags,
                          tag_mode=tag_mode, update=update, force=force,
                          provenance_source=provenance_source)


def write_document(doc: ConvertedDocument, source: Path, vault_path: Path,
                   topic: str | None = None, tags: list[str] | None = None,
                   tag_mode: str | None = None, update: bool = False,
                   force: bool = False,
                   provenance_source: str | None = None) -> DocumentIngest:
    """Write a converted document, archiving ``source`` as its original.

    Every check (existing archive, unsafe image path, hand-edited pages, topic,
    tags) runs before the first write, so a refusal leaves the vault unchanged.
    ``topic`` and ``tags`` apply to the parent and every chapter; tags are
    canonicalized once against the vault vocabulary. Appends one log entry.

    Without ``update`` a document already in the vault is refused. With it the
    new conversion replaces the document: every page is rewritten, chapter
    pages keep their ``created`` (matched by raw path), the parent keeps its
    ``created``, topic and tags unless new ones are given, and the pages, raws,
    sidecars and extracted images the new conversion no longer produces are
    deleted. A chapter or parent page edited by hand since it was written
    refuses the update with a diff unless ``force``. A conversion with no
    chapters is refused.
    """
    if not doc.chapters:
        raise ValueError(f"no content: {source.name} converted to no chapters")
    name = slugify(doc.title) or slugify(source.stem) or "document"
    if name in _RAW_NON_SOURCE_DIRS:
        # raw/<doc>/ holds the chapter raws, and raw/assets/ and raw/sessions/
        # hold other files an update would otherwise count as the document's.
        name = f"{name}-document"
    ext = source.suffix.lower()
    archive = vault_path / "raw" / "assets" / f"{name}{ext}"
    if not update:
        for existing in (archive, vault_path / "raw" / name):
            if existing.exists():
                raise FileExistsError(
                    f"{existing.relative_to(vault_path).as_posix()} already exists; "
                    f"pass --update to overwrite and update the document's pages"
                )
    assets_dir = vault_path / "raw" / "assets" / name
    members = [m for ch in doc.chapters for m in ch.images]
    if doc.cover and len(doc.chapters) > 1:
        members.append(doc.cover)
    for member in members:
        _check_member(member, assets_dir)

    vault_config = load_vault_config(vault_path)
    old = (_existing_set(vault_path, name, vault_config.get("topics", []))
           if update else _DocumentSet())
    titles = [f"{chapter.title} ({doc.title})" for chapter in doc.chapters]
    if len(doc.chapters) == 1:
        new_top = _rewrite_images(doc.chapters[0].markdown, doc.chapters[0].images,
                                  f"../raw/assets/{name}")
    else:
        cover = f"![cover](../raw/assets/{name}/{doc.cover})\n\n" if doc.cover else ""
        new_top = cover + "\n".join(f"- [[{t}]]" for t in titles) + "\n"
    if not force:
        _check_drift(vault_path, old, new_top)
    eff_tags = tags if tags is not None else old.tags

    if len(doc.chapters) == 1:
        chapter = doc.chapters[0]
        page = ingest_extracted(
            vault_path, source_url=provenance_source or str(source),
            content_type="application/octet-stream", asset=source.read_bytes(),
            markdown=new_top,
            extractor_title=doc.title, topic=topic or old.topic, tags=eff_tags,
            update=update, force=force, tag_mode=tag_mode,
            name=name, fetcher="local", asset_ext=ext,
        )
        _extract_images(doc, chapter.images, assets_dir)
        single_raw = vault_path / "raw" / f"{name}.md"
        _remove_stale(old, {page, single_raw, sidecar_path(single_raw)}, archive,
                      assets_dir, chapter.images)
        return DocumentIngest(page, 1)

    eff_topic = topic or old.topic or vault_config.get("default_topic")
    if not eff_topic:
        raise ValueError(
            "no topic: pass --topic or set default_topic in wiki.yaml "
            "(this vault declares no default_topic)"
        )
    canonical_tags = _resolve_tags(eff_tags or [], _load_vocab(vault_config, tag_mode))
    today = date.today().isoformat()
    total = len(doc.chapters)

    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(source.read_bytes())
    _extract_images(doc, members, assets_dir)

    raw_dir = vault_path / "raw" / name
    page_dir = vault_path / eff_topic / name
    raw_dir.mkdir(parents=True, exist_ok=True)
    page_dir.mkdir(parents=True, exist_ok=True)
    written: set[Path] = set()
    for chapter, title in zip(doc.chapters, titles):
        ordinal = format_ordinal(chapter.ordinal, total)
        slug = slugify(chapter.title)
        stem = f"{ordinal}-{slug}" if slug else ordinal
        body = _rewrite_images(chapter.markdown, chapter.images,
                               f"../../raw/assets/{name}").rstrip("\n") + "\n"
        raw = raw_dir / f"{stem}.md"
        raw.write_text(body)
        _write_sidecar(raw, source=provenance_source or str(source), fetcher="local")
        raw_ref = f"raw/{name}/{stem}.md"
        _write_page(page_dir / f"{stem}.md", {
            "title": title,
            "document": name,
            "chapter": chapter.ordinal,
            "topic": eff_topic,
            "tags": canonical_tags,
            "created": old.created_by_raw.get(raw_ref, today),
            "updated": today,
            "sources": [raw_ref],
        }, body)
        written.update({raw, sidecar_path(raw), page_dir / f"{stem}.md"})

    parent = vault_path / eff_topic / f"{name}.md"
    _write_page(parent, {
        "title": doc.title,
        "document": name,
        "topic": eff_topic,
        "tags": canonical_tags,
        "created": old.created or today,
        "updated": today,
        "sources": [f"raw/assets/{name}{ext}"],
        **doc.metadata,
    }, new_top)
    _remove_stale(old, written | {parent}, archive, assets_dir, members)
    append_log(vault_path, "update" if update else "ingest",
               f"{source.name} -> {eff_topic}/{name}.md ({total} chapters)")
    return DocumentIngest(parent, total)


@dataclass
class _DocumentSet:
    """The files a document already in the vault owns, as an update finds them.

    ``guarded`` holds (page, parsed page, raw ref) for every page generated from
    a raw (chapter pages, or the single page); ``parent`` the multi-chapter
    parent page, which has no text raw; ``files`` every page, raw and
    sidecar of the set; ``archive`` the archived original it records."""

    guarded: list[tuple[Path, dict, str | None]] = field(default_factory=list)
    parent: tuple[Path, dict] | None = None
    files: list[Path] = field(default_factory=list)
    dirs: list[Path] = field(default_factory=list)
    topic: str | None = None
    tags: list[str] | None = None
    created: str | None = None
    created_by_raw: dict[str, str] = field(default_factory=dict)
    archive: Path | None = None


def _document_archive(vault_path: Path, name: str, ref: str | None) -> Path | None:
    """``ref`` as a path when it is this document's archived original."""
    if not ref:
        return None
    path = PurePosixPath(ref)
    if (path.parent == PurePosixPath("raw/assets") and path.stem == name
            and path.suffix in DOCUMENT_SUFFIXES):
        return vault_path / path
    return None


def _existing_set(vault_path: Path, name: str, topics: list[str]) -> _DocumentSet:
    """Find the document ``name`` in the layout this module writes: the parent or
    single page at <topic>/<name>.md, chapter pages in <topic>/<name>/ that
    carry ``document: <name>``, raws and sidecars in raw/<name>/, and the single
    raw raw/<name>.md when its sidecar records this document's archive. Pages
    and raws that do not belong to the document are left out."""
    found = _DocumentSet()
    single_raw = vault_path / "raw" / f"{name}.md"
    single_ref = f"raw/{name}.md"
    single_archive = (_document_archive(vault_path, name,
                                        load_sidecar(single_raw).get("asset"))
                      if single_raw.is_file() else None)
    for topic in topics:
        top = vault_path / topic / f"{name}.md"
        if top.is_file():
            parsed = parse_page(top)
            meta = parsed["meta"] or {}
            sources = meta.get("sources") or []
            if meta.get("document") == name and "chapter" not in meta:
                found.parent = (top, parsed)
                for ref in sources:
                    found.archive = (found.archive
                                     or _document_archive(vault_path, name, ref))
            elif single_archive and single_ref in sources:
                found.guarded.append((top, parsed, single_ref))
                found.files += [single_raw, sidecar_path(single_raw)]
                found.archive = single_archive
            else:
                meta = None
            if meta is not None:
                found.files.append(top)
                found.topic = found.topic or meta.get("topic") or topic
                found.tags = meta.get("tags") or []
                found.created = meta.get("created")
        folder = vault_path / topic / name
        if folder.is_dir():
            found.dirs.append(folder)
            for page in sorted(folder.glob("*.md")):
                parsed = parse_page(page)
                meta = parsed["meta"] or {}
                if meta.get("document") != name or "chapter" not in meta:
                    continue
                ref = (meta.get("sources") or [None])[0]
                found.guarded.append((page, parsed, ref))
                found.files.append(page)
                found.topic = found.topic or meta.get("topic") or topic
                if ref and meta.get("created"):
                    found.created_by_raw[ref] = meta["created"]
    raw_dir = vault_path / "raw" / name
    if raw_dir.is_dir():
        found.dirs.append(raw_dir)
        found.files += [p for p in sorted(raw_dir.iterdir())
                        if p.is_file() and (p.suffix == ".md" or is_sidecar(p))]
    return found


def _check_drift(vault_path: Path, found: _DocumentSet, new_top: str) -> None:
    """Refuse, with a diff per page, when any page of ``found`` was edited by
    hand since it was written: its body no longer hashes to its stored
    render_hash (or, for an unstamped page, differs from its raw). The parent
    has no text raw, so it is checked by its render_hash only and diffed
    against ``new_top``, the body the write puts at its path. Reads only."""
    problems, diffs = [], []
    if found.parent is not None:
        page, parsed = found.parent
        body, stored = parsed["body"], (parsed["meta"] or {}).get("render_hash")
        if stored is not None and render_hash(body) != stored:
            rel = page.relative_to(vault_path).as_posix()
            lost = page_lines_lost(body, new_top)
            problems.append(f"page {rel} was edited by hand: {lost} page line(s) "
                            f"are not in the new conversion")
            diffs.append(page_raw_diff(body, new_top, f"page:{rel}", f"new:{rel}"))
    for page, parsed, raw_ref in found.guarded:
        meta, body = parsed["meta"] or {}, parsed["body"]
        raw_text = None
        raw = vault_path / raw_ref if raw_ref else None
        if raw is not None and raw.is_file():
            try:
                raw_text = raw.read_text()
            except UnicodeDecodeError:
                raw_text = None
        stored = meta.get("render_hash")
        if stored is not None:
            drifted = render_hash(body) != stored
        else:
            drifted = raw_text is not None and page_raw_diverged(body, raw_text)
        if not drifted:
            continue
        rel = page.relative_to(vault_path).as_posix()
        lost = page_lines_lost(body, raw_text or "")
        problems.append(f"page {rel} differs from {raw_ref}: {lost} page line(s) "
                        f"are not in the raw")
        diffs.append(page_raw_diff(body, raw_text or "", f"page:{rel}",
                                   f"raw:{raw_ref}"))
    if problems:
        raise PageDriftError(
            "; ".join(problems) + ". awiki treats pages as generated from raw and "
            "--update rewrites every page of the document from the new file, so "
            "review before overwriting: keep what is worth keeping elsewhere, then "
            "pass --force to discard the edits.",
            diff="".join(diffs),
        )


def _check_member(member: str, assets_dir: Path) -> None:
    """Refuse an image member that is not a plain relative path to an image
    file inside ``assets_dir``: absolute, drive or UNC paths, backslashes,
    '..', a suffix other than an image type, or a target that resolves outside
    the folder (a symlink already in it). Reads only."""
    path = PurePosixPath(member)
    win = PureWindowsPath(member)
    target = assets_dir / member
    if ("\\" in member or path.is_absolute() or ".." in path.parts
            or win.drive or win.root
            or path.suffix.lower() not in _IMAGE_SUFFIXES
            or not target.resolve().is_relative_to(assets_dir.resolve())):
        raise ValueError(f"unsafe image path in document: {member}")


def _remove_stale(found: _DocumentSet, keep: set[Path], archive: Path,
                  assets_dir: Path, members: list[str]) -> None:
    """Delete what the old set owned that the new write did not produce: its
    pages, raws and sidecars outside ``keep``, an archived original under
    another extension, and extracted images under ``assets_dir`` no chapter
    references. Folders left empty go too. No-op for a fresh ingest."""
    for path in found.files:
        if path not in keep and path.is_file():
            path.unlink()
    if found.archive is not None and found.archive != archive and found.archive.is_file():
        found.archive.unlink()
    if found.files and assets_dir.is_dir():
        wanted = {PurePosixPath(m) for m in members}
        for path in assets_dir.rglob("*"):
            if path.is_file() and PurePosixPath(
                    path.relative_to(assets_dir).as_posix()) not in wanted:
                path.unlink()
        found.dirs.append(assets_dir)
    for folder in found.dirs:
        for sub in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True):
            if not any(sub.iterdir()):
                sub.rmdir()
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _rewrite_images(markdown: str, images: list[str], prefix: str) -> str:
    """Point each ``![alt](<member>)`` reference at ``<prefix>/<member>``."""
    for member in images:
        markdown = re.sub(rf"(!\[[^\]]*\]\(){re.escape(member)}(?=[\s)])",
                          lambda m: f"{m.group(1)}{prefix}/{member}", markdown)
    return markdown


def _extract_images(doc: ConvertedDocument, members: list[str], assets_dir: Path) -> None:
    """Write each referenced member's bytes to ``assets_dir/<member path>``."""
    for member in dict.fromkeys(members):
        target = assets_dir / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(doc.assets[member])


def _write_page(path: Path, meta: dict, body: str) -> None:
    path.write_text(render_page(meta, body))
    stamp_render_hash(path)
