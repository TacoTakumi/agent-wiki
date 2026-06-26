"""Fetch + extract seam for URL ingest.

``Fetcher`` is the network boundary: given a URL it returns the raw bytes, the
content type, and the (possibly redirected) canonical URL. Only the built-in
``HttpFetcher`` is provided; the interface is the documented extension point for
a future agent-supplied fetcher (see REQ-06). Extraction (HTML -> clean
main-content markdown via trafilatura) runs on already-fetched bytes and makes
no network call, so the ingest pipeline reaches the network *only* through a
``Fetcher``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


class FetchError(Exception):
    """A fetch failed at the network/transport layer (connection, timeout, or a
    non-2xx status). The seam translates transport errors into this domain error
    so callers render a friendly message instead of leaking an httpx traceback."""


class UnsupportedContentType(ValueError):
    """A fetched content type the built-in fetcher does not extract. Only
    text-bearing types (HTML, and PDF once wired) are supported; image/audio/video
    and the like are unsupported rather than mis-ingested, pending a future
    agent-supplied fetcher (REQ-25)."""


def is_url(arg: str) -> bool:
    """True if ``arg`` is an http(s) URL (routes to the fetch pipeline); anything
    else is treated as a local filesystem path (REQ-05)."""
    return arg.startswith(("http://", "https://"))


@dataclass
class FetchResult:
    """What a ``Fetcher`` returns: the raw fetched bytes, the bare content type
    (no parameters), and the canonical/redirected URL the bytes came from."""

    body: bytes
    content_type: str
    source_url: str


class Fetcher(ABC):
    """Network boundary for URL ingest. Implementations turn a URL into bytes +
    content type + canonical URL. The ingest pipeline must reach the network only
    through this seam, so a future agent-supplied fetcher can replace it wholesale.
    """

    @abstractmethod
    def fetch(self, url: str) -> FetchResult: ...


class HttpFetcher(Fetcher):
    """Built-in fetcher over httpx; follows redirects and reports the final URL."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=self.timeout,
                             headers={"User-Agent": "agent-wiki (awiki)"})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FetchError(f"could not fetch {url}: {e}") from e
        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        return FetchResult(body=resp.content, content_type=content_type,
                           source_url=str(resp.url))


@dataclass
class ExtractResult:
    """Extracted clean markdown plus the extractor's title metadata (if any)."""

    markdown: str
    title: str | None


def extract(body: bytes | str, content_type: str) -> ExtractResult:
    """Extract clean main-content markdown from already-fetched bytes.

    HTML is routed to trafilatura. Other content types are unsupported here (PDF
    support arrives in a later task); raising keeps a mis-typed body from being
    mis-ingested.
    """
    if "html" in content_type:
        return _extract_html(body)
    raise UnsupportedContentType(
        f"unsupported content type: {content_type or 'unknown'}")


def _extract_html(body: bytes | str) -> ExtractResult:
    import trafilatura

    html = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    markdown = trafilatura.extract(html, output_format="markdown",
                                   include_formatting=True)
    if not markdown:
        raise ValueError("no main content could be extracted from the HTML")
    meta = trafilatura.extract_metadata(html)
    title = getattr(meta, "title", None) if meta else None
    return ExtractResult(markdown=markdown, title=title)
