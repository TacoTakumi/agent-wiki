import hashlib

import pytest
import yaml

from agent_wiki.fetch import (
    Fetcher, FetchResult, UnsupportedContentType, extract, is_url,
)
from agent_wiki.ingest import (
    UnchangedURLSkip, _resolve_title, ingest_url, normalize_url, url_to_name,
)
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


def test_title_precedence_extractor_metadata_first(tmp_vault):
    # Fixture 1: extractor title metadata present -> it wins over H1 and slug.
    assert _resolve_title("Extractor Title", "# An H1 Heading\n\nbody", "url-slug") \
        == "Extractor Title"


def test_title_precedence_falls_back_to_h1(tmp_vault):
    # Fixture 2: no extractor metadata, but a markdown H1 -> the H1 wins over slug.
    assert _resolve_title(None, "# An H1 Heading\n\nbody", "url-slug") == "An H1 Heading"
    assert _resolve_title("   ", "# An H1 Heading\n\nbody", "url-slug") == "An H1 Heading"


def test_title_precedence_falls_back_to_url_slug(tmp_vault):
    # Fixture 3: neither metadata nor H1 -> the URL-derived slug is the title.
    assert _resolve_title(None, "plain body with no heading at all", "url-slug") == "url-slug"


def test_ingest_url_page_title_uses_extractor_metadata(tmp_vault):
    # End-to-end: when the extractor reports a title, the page title equals it.
    url = "https://example.com/great-article"
    fetcher = FakeFetcher(ARTICLE_HTML, source_url=url)
    expected = extract(ARTICLE_HTML, "text/html").title
    page = ingest_url(url, tmp_vault, topic="research", fetcher=fetcher)
    assert parse_page(page)["meta"]["title"] == expected


def test_extract_rejects_non_html_content_type():
    with pytest.raises(ValueError):
        extract(b"%PDF-1.4 fake pdf bytes", "application/pdf")


@pytest.mark.parametrize("ctype", ["image/png", "audio/mpeg", "video/mp4"])
def test_unsupported_content_type_writes_nothing(tmp_vault, ctype):
    url = "https://example.com/media-file"
    fetcher = FakeFetcher(b"\x00\x01\x02binary-bytes", content_type=ctype, source_url=url)
    with pytest.raises(UnsupportedContentType):
        ingest_url(url, tmp_vault, topic="research", fetcher=fetcher)

    # No page, no raw body, no archived asset written for an unsupported type.
    assert list((tmp_vault / "research").glob("*.md")) == []
    assert not (tmp_vault / "raw" / "assets").exists()
    assert [p for p in (tmp_vault / "raw").iterdir() if p.is_file()] == []


def test_unsupported_html_still_ingests(tmp_vault):
    url = "https://example.com/great-article"
    page = ingest_url(url, tmp_vault, topic="research",
                      fetcher=FakeFetcher(ARTICLE_HTML, source_url=url))
    assert page.exists()


def test_dedup_unchanged_url_skips_without_rewrite(tmp_vault):
    url = "https://example.com/great-article"
    page = ingest_url(url, tmp_vault, topic="research",
                      fetcher=FakeFetcher(ARTICLE_HTML, source_url=url))
    before = page.read_text()

    with pytest.raises(UnchangedURLSkip):
        ingest_url(url, tmp_vault, topic="research",
                   fetcher=FakeFetcher(ARTICLE_HTML, source_url=url))
    assert page.read_text() == before  # page not rewritten


def test_dedup_force_rewrites_even_when_unchanged(tmp_vault):
    url = "https://example.com/great-article"
    ingest_url(url, tmp_vault, topic="research",
               fetcher=FakeFetcher(ARTICLE_HTML, source_url=url))
    page = ingest_url(url, tmp_vault, topic="research",
                      fetcher=FakeFetcher(ARTICLE_HTML, source_url=url), force=True)
    assert page.exists()
    assert "widgets and gadgets" in page.read_text()


def test_dedup_changed_body_rewrites(tmp_vault):
    url = "https://example.com/great-article"
    ingest_url(url, tmp_vault, topic="research",
               fetcher=FakeFetcher(ARTICLE_HTML, source_url=url))
    changed = ARTICLE_HTML.replace("meaningful article body about widgets and gadgets",
                                   "a substantially different body of text now")
    page = ingest_url(url, tmp_vault, topic="research",
                      fetcher=FakeFetcher(changed, source_url=url))
    assert "a substantially different body of text now" in page.read_text()


def test_dedup_cli_unchanged_skips_with_exit_zero(tmp_config, monkeypatch):
    from click.testing import CliRunner
    import agent_wiki.fetch as fetchmod
    from agent_wiki.cli import cli

    class CannedFetcher:
        def fetch(self, url):
            return FetchResult(ARTICLE_HTML.encode(), "text/html", url)

    monkeypatch.setattr(fetchmod, "HttpFetcher", CannedFetcher)

    runner = CliRunner()
    url = "https://example.com/great-article"
    first = runner.invoke(cli, ["ingest", url, "-t", "research"])
    assert first.exit_code == 0, first.output
    again = runner.invoke(cli, ["ingest", url, "-t", "research"])
    assert again.exit_code == 0, again.output
    assert "unchanged" in again.output.lower()


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


def test_normalize_url_rules():
    # lowercase scheme+host, strip default ports, drop fragment, trim trailing slash
    assert normalize_url("HTTP://Example.com:80/Foo/") == "http://example.com/Foo"
    assert normalize_url("https://Example.com:443/a#frag") == "https://example.com/a"
    # query string kept verbatim (v1)
    assert normalize_url("https://example.com/a?b=c") == "https://example.com/a?b=c"


def test_ingest_url_name_normalizes_trailing_slash(tmp_vault):
    fetcher = FakeFetcher(ARTICLE_HTML, source_url="https://example.com/great-article/")
    ingest_url("https://example.com/great-article/", tmp_vault,
               topic="research", fetcher=fetcher)
    assert (tmp_vault / "raw" / "great-article.md").exists()


def test_ingest_url_normalized_dedup_same_url_across_title_change(tmp_vault):
    url = "https://example.com/great-article"
    first = FakeFetcher(ARTICLE_HTML, source_url=url)
    p1 = ingest_url(url, tmp_vault, topic="research", fetcher=first)

    # Same URL, different article title -> must resolve to the SAME page, no orphan.
    html2 = ARTICLE_HTML.replace("The Great Article", "A Completely Renamed Article")
    second = FakeFetcher(html2, source_url=url)
    p2 = ingest_url(url, tmp_vault, topic="research", fetcher=second)

    assert p1 == p2
    pages = list((tmp_vault / "research").glob("*.md"))
    assert pages == [p2]
    assert parse_page(p2)["meta"]["title"] == "A Completely Renamed Article"


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
