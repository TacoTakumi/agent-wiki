"""File a converted local document (PDF, HTML, EPUB) into the vault.

A document with one chapter becomes a single page through ``ingest_extracted``
(raw/<doc>.md, raw/assets/<doc>.<ext>, <topic>/<doc>.md). From two chapters up
it is laid out as:

- raw/assets/<doc>.<ext>            the original, byte-identical
- raw/assets/<doc>/<member path>    each image a chapter references, and the cover
- raw/<doc>/NN-<chapter-slug>.md    one raw per chapter, with its sidecar
- <topic>/<doc>.md                  the parent: metadata, cover line, chapter links
- <topic>/<doc>/NN-<chapter-slug>.md one page per chapter

where <doc> is the slug of the document title. A chapter raw holds exactly the
chapter page body, image references included (written relative to the page),
so rebuilding a chapter page from its raw is a plain render.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from agent_wiki.config import load_vault_config
from agent_wiki.convert.model import ConvertedDocument
from agent_wiki.convert.split import format_ordinal
from agent_wiki.ingest import (
    _load_vocab, _resolve_tags, _write_sidecar, ingest_extracted,
)
from agent_wiki.log import append_log
from agent_wiki.page import render_page, slugify, stamp_render_hash

DOCUMENT_SUFFIXES = frozenset({".pdf", ".html", ".htm", ".epub"})


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
                    tags: list[str] | None = None,
                    tag_mode: str | None = None,
                    provenance_source: str | None = None) -> DocumentIngest:
    """Convert ``source`` and write it into the vault (see ``write_document``).
    The PDF extractor is the vault's ``pdf_extractor`` setting.
    ``provenance_source`` is the path the sidecars record instead of ``source``."""
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")
    pdf_extractor = load_vault_config(vault_path).get("pdf_extractor", "pymupdf4llm")
    doc = convert_document(source, pdf_extractor=pdf_extractor)
    return write_document(doc, source, vault_path, topic=topic, tags=tags,
                          tag_mode=tag_mode, provenance_source=provenance_source)


def write_document(doc: ConvertedDocument, source: Path, vault_path: Path,
                   topic: str | None = None, tags: list[str] | None = None,
                   tag_mode: str | None = None,
                   provenance_source: str | None = None) -> DocumentIngest:
    """Write a converted document, archiving ``source`` as its original.

    Every check (existing archive, unsafe image path, topic, tags) runs before
    the first write, so a refusal leaves the vault unchanged. ``topic`` and
    ``tags`` apply to the parent and every chapter; tags are canonicalized once
    against the vault vocabulary. Appends one log entry.
    """
    name = slugify(doc.title) or slugify(source.stem) or "document"
    ext = source.suffix.lower()
    archive = vault_path / "raw" / "assets" / f"{name}{ext}"
    if archive.exists():
        raise FileExistsError(
            f"raw/assets/{name}{ext} already exists; pass --update to overwrite "
            f"and update the document's pages"
        )
    assets_dir = vault_path / "raw" / "assets" / name
    members = [m for ch in doc.chapters for m in ch.images]
    if doc.cover and len(doc.chapters) > 1:
        members.append(doc.cover)
    for member in members:
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe image path in document: {member}")

    if len(doc.chapters) == 1:
        chapter = doc.chapters[0]
        page = ingest_extracted(
            vault_path, source_url=provenance_source or str(source),
            content_type="application/octet-stream", asset=source.read_bytes(),
            markdown=_rewrite_images(chapter.markdown, chapter.images,
                                     f"../raw/assets/{name}"),
            extractor_title=doc.title, topic=topic, tags=tags, tag_mode=tag_mode,
            name=name, fetcher="local", asset_ext=ext,
        )
        _extract_images(doc, chapter.images, assets_dir)
        return DocumentIngest(page, 1)

    vault_config = load_vault_config(vault_path)
    eff_topic = topic or vault_config.get("default_topic")
    if not eff_topic:
        raise ValueError(
            "no topic: pass --topic or set default_topic in wiki.yaml "
            "(this vault declares no default_topic)"
        )
    canonical_tags = _resolve_tags(tags or [], _load_vocab(vault_config, tag_mode))
    today = date.today().isoformat()
    total = len(doc.chapters)

    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(source.read_bytes())
    _extract_images(doc, members, assets_dir)

    raw_dir = vault_path / "raw" / name
    page_dir = vault_path / eff_topic / name
    raw_dir.mkdir(parents=True, exist_ok=True)
    page_dir.mkdir(parents=True, exist_ok=True)
    links = []
    for chapter in doc.chapters:
        ordinal = format_ordinal(chapter.ordinal, total)
        slug = slugify(chapter.title)
        stem = f"{ordinal}-{slug}" if slug else ordinal
        body = _rewrite_images(chapter.markdown, chapter.images,
                               f"../../raw/assets/{name}").rstrip("\n") + "\n"
        raw = raw_dir / f"{stem}.md"
        raw.write_text(body)
        _write_sidecar(raw, source=provenance_source or str(source), fetcher="local")
        title = f"{chapter.title} ({doc.title})"
        _write_page(page_dir / f"{stem}.md", {
            "title": title,
            "document": name,
            "chapter": chapter.ordinal,
            "topic": eff_topic,
            "tags": canonical_tags,
            "created": today,
            "updated": today,
            "sources": [f"raw/{name}/{stem}.md"],
        }, body)
        links.append(f"- [[{title}]]")

    cover = f"![cover](../raw/assets/{name}/{doc.cover})\n\n" if doc.cover else ""
    parent = vault_path / eff_topic / f"{name}.md"
    _write_page(parent, {
        "title": doc.title,
        "document": name,
        "topic": eff_topic,
        "tags": canonical_tags,
        "created": today,
        "updated": today,
        "sources": [f"raw/assets/{name}{ext}"],
        **doc.metadata,
    }, cover + "\n".join(links) + "\n")
    append_log(vault_path, "ingest",
               f"{source.name} -> {eff_topic}/{name}.md ({total} chapters)")
    return DocumentIngest(parent, total)


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
