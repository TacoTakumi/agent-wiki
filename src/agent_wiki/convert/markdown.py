"""Shared HTML to markdown renderer for the document converters.

One markdownify configuration for every format: ATX headings (``## Title``),
dash bullets, ``*em*`` and ``**strong**``. The caller can map each image
``src`` to the path the chapter should reference; the renderer reports the
mapped paths so the converter knows which image files to extract.
"""
from __future__ import annotations

import re
from typing import Callable

from markdownify import ATX, MarkdownConverter


class _Converter(MarkdownConverter):
    def __init__(self, resolve_image: Callable[[str], str | None] | None):
        super().__init__(heading_style=ATX, bullets="-")
        self._resolve_image = resolve_image
        self.images: list[str] = []

    def convert_img(self, el, text, parent_tags):
        target = None
        if self._resolve_image is not None:
            target = self._resolve_image(el.attrs.get("src") or "")
            if target is not None:
                el.attrs["src"] = target
        out = super().convert_img(el, text, parent_tags)
        # Inside a heading or table cell markdownify writes only the alt text,
        # so the image is not referenced and needs no extraction.
        if target is not None and out.startswith("![") and target not in self.images:
            self.images.append(target)
        return out


def html_to_markdown(
    html: str,
    resolve_image: Callable[[str], str | None] | None = None,
) -> tuple[str, list[str]]:
    """Render an HTML fragment or document as markdown.

    ``resolve_image(src)`` returns the path to write in place of an img
    ``src``, or None to keep the ``src`` as it is and not report it. Returns
    the markdown (blank-line runs collapsed, no surrounding whitespace) and
    the mapped image paths in first-reference order, without duplicates.
    """
    conv = _Converter(resolve_image)
    md = re.sub(r"\n{3,}", "\n\n", conv.convert(html)).strip()
    return md, conv.images
