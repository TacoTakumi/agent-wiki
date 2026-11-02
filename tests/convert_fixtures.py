"""Builders for the small EPUB, HTML and PDF files the convert tests ingest.

Every file is built in-test (no binary fixtures in the repo). Import from a
test with ``from convert_fixtures import build_epub`` (pytest puts tests/ on
sys.path, as for ``conftest``).

Default EPUB: title 'A Book', author, language, publisher, an EPUB2 cover, an
NCX and an EPUB3 nav, and three chapters in three XHTML files. Chapter 1
references ``OEBPS/images/a.jpg``; ``OEBPS/images/b.png`` is in the archive
but no chapter references it. Pass ``files`` and ``toc`` to build any other
shape (several chapters in one file, nested entries, nav and NCX that differ).
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf

OPF_DIR = "OEBPS"


def image_bytes(fmt: str = "png", shade: int = 200) -> bytes:
    """A real 2x2 image in ``fmt`` ('png' or 'jpg'); ``shade`` varies bytes."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), 0)
    pix.clear_with(shade)
    return pix.tobytes(fmt)


# ---------------------------------------------------------------- EPUB

@dataclass
class XhtmlFile:
    """One spine document. ``name`` is relative to OEBPS (``text/ch01.xhtml``)
    and ``body`` is the inner markup of <body>."""

    name: str
    body: str
    title: str = ""


@dataclass
class NavPoint:
    """One TOC entry. ``href`` is relative to OEBPS (``text/ch01.xhtml#c1``)."""

    title: str
    href: str
    children: list[NavPoint] = field(default_factory=list)


DEFAULT_CHAPTER_TITLES = ["Chapter One", "Chapter Two", "Chapter Three",
                          "Chapter Four", "Chapter Five", "Chapter Six"]


def chapter_title(n: int) -> str:
    """The default title of chapter ``n`` (1-based)."""
    if n <= len(DEFAULT_CHAPTER_TITLES):
        return DEFAULT_CHAPTER_TITLES[n - 1]
    return f"Chapter {n}"


def chapter_body(n: int, title: str | None = None,
                 images: tuple[str, ...] | list[str] = ()) -> str:
    """XHTML body for chapter ``n``: an h2 with fragment id ``c<n>``, an h3 with
    id ``c<n>s1``, a paragraph holding the marker ``BODY<n>``, and one <img>
    per entry of ``images`` (paths relative to the chapter file)."""
    title = title or chapter_title(n)
    imgs = "".join(f'<p><img src="{escape(src)}" alt="figure {i + 1}"/></p>\n'
                   for i, src in enumerate(images))
    return (
        f'<h2 id="c{n}">{escape(title)}</h2>\n'
        f"<p>Opening text of chapter {n}. BODY{n}</p>\n"
        f"{imgs}"
        f'<h3 id="c{n}s1">Section {n}.1</h3>\n'
        f"<p>Some <em>emphasis</em>, some <strong>strength</strong> and "
        f'<a href="https://example.com/{n}">a link</a>.</p>\n'
        f"<ol><li>first</li><li>second</li></ol>\n"
        f"<ul><li>apple</li><li>pear</li></ul>\n"
    )


def default_epub_content(
    n_chapters: int = 3,
    image_chapters: dict[int, list[str]] | None = None,
) -> tuple[list[XhtmlFile], list[NavPoint]]:
    """Files and TOC for ``n_chapters`` chapters, one file each.

    ``image_chapters`` maps a chapter number to the image paths (relative to
    OEBPS) its body references; the default is ``{1: ['images/a.jpg']}``.
    """
    if image_chapters is None:
        image_chapters = {1: ["images/a.jpg"]}
    files, toc = [], []
    for n in range(1, n_chapters + 1):
        name = f"text/ch{n:02d}.xhtml"
        # Chapter files live in text/, so image paths climb one level.
        srcs = [f"../{p}" for p in image_chapters.get(n, [])]
        files.append(XhtmlFile(name, chapter_body(n, images=srcs),
                               chapter_title(n)))
        toc.append(NavPoint(chapter_title(n), f"{name}#c{n}"))
    return files, toc


def _xhtml(title: str, body: str, extra_ns: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{extra_ns}>\n'
        f"<head><title>{escape(title)}</title></head>\n"
        f"<body>\n{body}</body>\n</html>\n"
    )


def _ncx(title: str, toc: list[NavPoint]) -> str:
    counter = [0]

    def points(entries: list[NavPoint]) -> str:
        out = []
        for e in entries:
            counter[0] += 1
            i = counter[0]
            out.append(
                f'<navPoint id="np{i}" playOrder="{i}">'
                f"<navLabel><text>{escape(e.title)}</text></navLabel>"
                f'<content src="{escape(e.href)}"/>'
                f"{points(e.children)}</navPoint>\n")
        return "".join(out)

    nav_map = points(toc)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '<head><meta name="dtb:uid" content="urn:uuid:fixture-book"/></head>\n'
        f"<docTitle><text>{escape(title)}</text></docTitle>\n"
        f"<navMap>\n{nav_map}</navMap>\n</ncx>\n"
    )


def _nav(toc: list[NavPoint]) -> str:
    def items(entries: list[NavPoint]) -> str:
        lis = "".join(
            f'<li><a href="{escape(e.href)}">{escape(e.title)}</a>'
            f"{items(e.children) if e.children else ''}</li>"
            for e in entries)
        return f"<ol>{lis}</ol>"

    body = f'<nav epub:type="toc" id="toc"><h1>Contents</h1>{items(toc)}</nav>\n'
    return _xhtml("Contents", body,
                  ' xmlns:epub="http://www.idpf.org/2007/ops"')


def _media_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "svg": "image/svg+xml",
            "webp": "image/webp"}.get(ext, "application/octet-stream")


def build_epub(
    path: Path,
    *,
    title: str | None = "A Book",
    author: str | None = "Jane Author",
    language: str | None = "en",
    publisher: str | None = "Fixture Press",
    n_chapters: int = 3,
    files: list[XhtmlFile] | None = None,
    toc: list[NavPoint] | None = None,
    nav_toc: list[NavPoint] | None = None,
    ncx: bool = True,
    nav: bool = True,
    cover: str | None = "epub2",
    images: dict[str, bytes] | None = None,
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    """Write an EPUB to ``path`` and return it.

    ``files``/``toc`` default to ``default_epub_content(n_chapters)``. The NCX
    and the nav both list ``toc`` unless ``nav_toc`` gives the nav its own
    entries; ``ncx=False``/``nav=False`` leave either out. ``cover`` is
    'epub2' (``<meta name="cover">``), 'epub3' (``properties="cover-image"``)
    or None; the cover image is ``OEBPS/images/cover.jpg``. ``images`` maps
    paths relative to OEBPS to bytes and defaults to ``images/a.jpg`` and
    ``images/b.png``. ``extra_members`` adds raw zip members (full archive
    paths) without listing them in the manifest. A None metadata field is
    left out of the OPF.
    """
    path = Path(path)
    if files is None:
        files, default_toc = default_epub_content(n_chapters)
        toc = default_toc if toc is None else toc
    toc = toc or []
    if images is None:
        images = {"images/a.jpg": image_bytes("jpg", 120),
                  "images/b.png": image_bytes("png", 60)}
    images = dict(images)
    if cover:
        images.setdefault("images/cover.jpg", image_bytes("jpg", 240))
    epub3 = nav or cover == "epub3"

    manifest, spine = [], []
    if ncx:
        manifest.append('<item id="ncx" href="toc.ncx" '
                        'media-type="application/x-dtbncx+xml"/>')
    if nav:
        manifest.append('<item id="nav" href="nav.xhtml" '
                        'media-type="application/xhtml+xml" properties="nav"/>')
    for i, f in enumerate(files, 1):
        manifest.append(f'<item id="doc{i}" href="{escape(f.name)}" '
                        'media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="doc{i}"/>')
    for i, name in enumerate(images, 1):
        item_id = "cover-img" if name == "images/cover.jpg" else f"img{i}"
        props = (' properties="cover-image"'
                 if cover == "epub3" and item_id == "cover-img" else "")
        manifest.append(f'<item id="{item_id}" href="{escape(name)}" '
                        f'media-type="{_media_type(name)}"{props}/>')

    meta = ['<dc:identifier id="uid">urn:uuid:fixture-book</dc:identifier>']
    for tag, value in (("title", title), ("creator", author),
                       ("language", language), ("publisher", publisher)):
        if value is not None:
            meta.append(f"<dc:{tag}>{escape(value)}</dc:{tag}>")
    if cover == "epub2":
        meta.append('<meta name="cover" content="cover-img"/>')
    if epub3:
        meta.append('<meta property="dcterms:modified">'
                    "2026-01-01T00:00:00Z</meta>")

    spine_attr = ' toc="ncx"' if ncx else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<package xmlns="http://www.idpf.org/2007/opf" '
        f'version="{"3.0" if epub3 else "2.0"}" unique-identifier="uid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">\n'
        + "\n".join(meta) + "\n</metadata>\n"
        "<manifest>\n" + "\n".join(manifest) + "\n</manifest>\n"
        f"<spine{spine_attr}>\n" + "\n".join(spine) + "\n</spine>\n"
        "</package>\n"
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        f'<rootfiles><rootfile full-path="{OPF_DIR}/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n'
        "</container>\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        # The mimetype member comes first and uncompressed, per the OCF spec.
        zf.writestr("mimetype", "application/epub+zip",
                    compress_type=zipfile.ZIP_STORED)
        z = zipfile.ZIP_DEFLATED
        zf.writestr("META-INF/container.xml", container, compress_type=z)
        zf.writestr(f"{OPF_DIR}/content.opf", opf, compress_type=z)
        if ncx:
            zf.writestr(f"{OPF_DIR}/toc.ncx", _ncx(title or "", toc),
                        compress_type=z)
        if nav:
            zf.writestr(f"{OPF_DIR}/nav.xhtml",
                        _nav(toc if nav_toc is None else nav_toc),
                        compress_type=z)
        for f in files:
            zf.writestr(f"{OPF_DIR}/{f.name}", _xhtml(f.title, f.body),
                        compress_type=z)
        for name, data in images.items():
            zf.writestr(f"{OPF_DIR}/{name}", data, compress_type=z)
        for name, data in (extra_members or {}).items():
            zf.writestr(name, data, compress_type=z)
    return path


# ---------------------------------------------------------------- HTML

DEFAULT_HTML_SECTIONS = [
    (1, "Sample Document", "Intro text before the first section. INTRO"),
    (2, "First Section", "Text of the first section. SECTION1"),
    (2, "Second Section", "Text of the second section. SECTION2"),
    (2, "Third Section", "Text of the third section. SECTION3"),
    (2, "Fourth Section", "Text of the fourth section. SECTION4"),
]


def build_html(
    path: Path,
    *,
    title: str | None = "Sample Document",
    sections: list[tuple[int, str, str]] | None = None,
    nav: bool = True,
    image: str | None = "images/pic.png",
) -> Path:
    """Write a single HTML file to ``path`` and return it.

    ``sections`` is a list of (heading level, heading text, paragraph text);
    the default is one h1 then four h2 sections. ``title`` None leaves out
    <title>. ``nav`` adds a <nav> block (marker NAVTEXT) before the first
    heading and a <footer> (marker FOOTTEXT) after the last section.
    ``image`` puts an <img> with that relative src in the first section and
    writes a real PNG there next to the HTML file.
    """
    path = Path(path)
    sections = DEFAULT_HTML_SECTIONS if sections is None else sections
    parts = []
    if nav:
        parts.append('<nav><a href="#">Home</a> NAVTEXT</nav>')
    for i, (level, heading, text) in enumerate(sections):
        parts.append(f'<h{level} id="s{i}">{escape(heading)}</h{level}>')
        parts.append(f"<p>{escape(text)}</p>")
        if i == 0 and image:
            parts.append(f'<p><img src="{escape(image)}" alt="picture"></p>')
    if nav:
        parts.append("<footer>FOOTTEXT</footer>")
    head = f"<title>{escape(title)}</title>" if title is not None else ""
    html = ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
            f"{head}</head>\n<body>\n" + "\n".join(parts)
            + "\n</body></html>\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    if image:
        img = path.parent / image
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(image_bytes("png"))
    return path


# ---------------------------------------------------------------- PDF

DEFAULT_PDF_TOC = [[1, "Chapter One", 1], [1, "Chapter Two", 3],
                   [1, "Chapter Three", 5]]


def build_pdf(
    path: Path,
    *,
    title: str = "A PDF Book",
    n_pages: int = 6,
    toc: list[list] | None = None,
    outline: bool = True,
) -> Path:
    """Write a PDF of ``n_pages`` pages to ``path`` and return it.

    ``toc`` is a pymupdf outline list of [level, title, page] (1-based pages)
    and defaults to three chapters on pages 1, 3 and 5. A page where an entry
    starts shows the entry title as a large heading. Every page holds the
    marker ``PAGE<n>``. ``outline=False`` writes no outline; ``title`` ''
    leaves the metadata title empty.
    """
    path = Path(path)
    toc = DEFAULT_PDF_TOC if toc is None else toc
    starts = {page: t for _level, t, page in toc}
    doc = pymupdf.open()
    for n in range(1, n_pages + 1):
        page = doc.new_page()
        y = 72
        if n in starts:
            page.insert_text((72, y), starts[n], fontsize=20)
            y += 36
        page.insert_text((72, y), f"Text on page {n}. PAGE{n}", fontsize=11)
    if outline and toc:
        doc.set_toc(toc)
    doc.set_metadata({"title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path
