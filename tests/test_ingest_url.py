import hashlib

import pytest
import yaml

from agent_wiki.fetch import Fetcher, FetchResult, extract, is_url
from agent_wiki.ingest import ingest_url, url_to_name
from agent_wiki.page import parse_page, sidecar_path

# A small HTML page: real article content wrapped in nav/footer boilerplate that a
# main-content extractor must strip.
ARTICLE_HTML = """<html><head><title>The Great Article</title></head>
<body>
<nav>Home About Contact NAVLINKZZZ</nav>
<main><article>
<h1>The Great Article</h1>
<p>This is the meaningful article body about widgets and gadgets, exploring how
they interconnect in modern systems.</p>
<h2>Background</h2>
<p>A second paragraph with more substance, describing the history and the
rationale behind the design choices.</p>
<ul><li>First point worth noting</li><li>Second point worth noting</li></ul>
</article></main>
<footer>Copyright 2026 FOOTERJUNKZZZ</footer>
</body></html>"""


class FakeFetcher(Fetcher):
    """Test seam: returns canned bytes/content-type/URL with no network access."""

    def __init__(self, body, content_type="text/html",
                 source_url="https://example.com/great-article"):
        self.body = body.encode() if isinstance(body, str) else body
        self.content_type = content_type
        self.source_url = source_url
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        return FetchResult(self.body, self.content_type, self.source_url)


@pytest.mark.parametrize("arg,expected", [
    ("https://example.com/a", True),
    ("http://example.com/a", True),
    ("/home/user/notes.md", False),
    ("notes.md", False),
    ("ftp://example.com/x", False),
    ("./relative/path", False),
])
def test_is_url_predicate(arg, expected):
    assert is_url(arg) is expected


def test_ingest_url_html_creates_page_raw_and_sidecar(tmp_vault):
    fetcher = FakeFetcher(ARTICLE_HTML, source_url="https://example.com/great-article")
    page = ingest_url("https://example.com/great-article", tmp_vault,
                      topic="research", fetcher=fetcher)

    # A page was created carrying the article text.
    parsed = parse_page(page)
    assert "widgets and gadgets" in parsed["body"]
    raw_ref = parsed["meta"]["sources"][0]
    assert raw_ref.startswith("raw/") and raw_ref.endswith(".md")

    # Raw markdown holds the article and excludes nav/footer boilerplate.
    raw_path = tmp_vault / raw_ref
    raw_text = raw_path.read_text()
    assert "widgets and gadgets" in raw_text
    assert "NAVLINKZZZ" not in raw_text
    assert "FOOTERJUNKZZZ" not in raw_text

    # Sidecar records http provenance and a hash of the stored raw body.
    sidecar = yaml.safe_load(sidecar_path(raw_path).read_text())
    assert sidecar["source"] == "https://example.com/great-article"
    assert sidecar["fetcher"] == "http"
    assert sidecar["ingested"]
    assert sidecar["sha256"] == hashlib.sha256(raw_path.read_bytes()).hexdigest()

    # The fetch went through the injected seam, exactly once.
    assert fetcher.calls == ["https://example.com/great-article"]


def test_ingest_url_raw_name_derives_from_url(tmp_vault):
    # The raw stem comes from the URL path, not the article's title.
    fetcher = FakeFetcher(ARTICLE_HTML, source_url="https://example.com/great-article")
    ingest_url("https://example.com/great-article", tmp_vault,
               topic="research", fetcher=fetcher)
    assert (tmp_vault / "raw" / "great-article.md").exists()


def test_url_to_name_basics():
    assert url_to_name("https://example.com/foo/bar-baz") == "bar-baz"
    assert url_to_name("https://example.com/page.html") == "page"
    assert url_to_name("https://example.com/") == "examplecom"


def test_extract_rejects_non_html_content_type():
    with pytest.raises(ValueError):
        extract(b"%PDF-1.4 fake pdf bytes", "application/pdf")


def test_ingest_url_page_carries_inline_source_url(tmp_vault):
    url = "https://example.com/great-article"
    fetcher = FakeFetcher(ARTICLE_HTML, source_url=url)
    page = ingest_url(url, tmp_vault, topic="research", fetcher=fetcher)

    meta = parse_page(page)["meta"]
    assert meta["source_url"] == url
    # Operational provenance stays in the sidecar, never on the page.
    for op in ("sha256", "fetcher", "ingested", "asset_sha256"):
        assert op not in meta


def test_reingest_url_keeps_inline_source_url(tmp_vault):
    url = "https://example.com/great-article"
    fetcher = FakeFetcher(ARTICLE_HTML, source_url=url)
    ingest_url(url, tmp_vault, topic="research", fetcher=fetcher)
    # Re-ingesting the same URL still carries source_url on the page.
    page = ingest_url(url, tmp_vault, topic="research", fetcher=fetcher,
                      update=True, force=True)
    assert parse_page(page)["meta"]["source_url"] == url


def test_ingest_url_archives_original_asset(tmp_vault):
    url = "https://example.com/great-article"
    fetcher = FakeFetcher(ARTICLE_HTML, source_url=url)
    page = ingest_url(url, tmp_vault, topic="research", fetcher=fetcher)

    asset = tmp_vault / "raw" / "assets" / "great-article.html"
    assert asset.exists()
    # The archived asset is byte-identical to the fetched original.
    assert asset.read_bytes() == ARTICLE_HTML.encode()

    raw_path = tmp_vault / parse_page(page)["meta"]["sources"][0]
    sidecar = yaml.safe_load(sidecar_path(raw_path).read_text())
    assert sidecar["asset"] == "raw/assets/great-article.html"
    assert sidecar["asset_sha256"] == hashlib.sha256(asset.read_bytes()).hexdigest()
    # The asset hash (original HTML) differs from the raw-body hash (extracted md).
    assert sidecar["asset_sha256"] != sidecar["sha256"]
