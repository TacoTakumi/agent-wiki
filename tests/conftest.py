import os
import pytest
import yaml
import json as _json


# A small HTML page (real article wrapped in nav/footer boilerplate) used by the
# URL-ingest service/parity tests. A unique body marker proves main-content
# extraction ran; NAV/FOOT markers prove boilerplate was stripped.
URL_SAMPLE_HTML = (
    "<html><head><title>Sample URL Page</title></head>\n"
    "<body>\n"
    "<nav>Home About Contact NAVZZZ</nav>\n"
    "<main><article>\n"
    "<h1>Sample URL Page</h1>\n"
    "<p>A unique body marker about parity widgets for the url ingest test, "
    "exploring how they interconnect across the remote and local paths.</p>\n"
    "<h2>Background</h2>\n"
    "<p>A second paragraph with more substance so the main-content extractor has "
    "enough to anchor on and confidently drop the surrounding chrome.</p>\n"
    "<ul><li>First point worth noting</li><li>Second point worth noting</li></ul>\n"
    "</article></main>\n"
    "<footer>Copyright 2026 FOOTZZZ</footer>\n"
    "</body></html>"
)


@pytest.fixture
def url_fetcher_cls():
    """A Fetcher class (constructor-compatible with HttpFetcher) returning canned
    HTML with no network — monkeypatch agent_wiki.fetch.HttpFetcher with it so the
    CLIENT-side fetch is stubbed while the SERVER still performs no fetch at all."""
    from agent_wiki.fetch import Fetcher, FetchResult

    class _CannedFetcher(Fetcher):
        HTML = URL_SAMPLE_HTML  # the original bytes the server should archive

        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, url):
            return FetchResult(URL_SAMPLE_HTML.encode(), "text/html", url)

    return _CannedFetcher


@pytest.fixture
def tmp_vault(tmp_path):
    """Create a temporary vault directory with wiki.yaml and standard structure."""
    vault = tmp_path / "vault"
    vault.mkdir()

    config = {
        "vault": {"name": "Test Wiki", "version": 1},
        "topics": ["projects", "decisions", "research", "tools"],
        "default_topic": "research",
    }
    (vault / "wiki.yaml").write_text(yaml.dump(config))
    (vault / "raw").mkdir()
    (vault / "index.md").write_text("# Index\n")
    (vault / "log.md").write_text("# Activity Log\n")

    for topic in config["topics"]:
        (vault / topic).mkdir()

    return vault


@pytest.fixture
def tmp_config(tmp_path, tmp_vault, monkeypatch):
    """Create a temporary user config pointing to tmp_vault."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(yaml.dump({"vault_path": str(tmp_vault)}))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    return config_file


@pytest.fixture
def tmp_settings(tmp_path):
    """Create an empty settings.json file and return its path.

    Used to test `awiki hook install --config-path <path>`.
    """
    settings_path = tmp_path / "claude-settings.json"
    settings_path.write_text(_json.dumps({}))
    return settings_path


@pytest.fixture
def server_tokens():
    from agent_wiki.server_config import hash_token
    return {
        "reader-tok": "reader",
        "writer-tok": "writer",
        "admin-tok": "admin",
    }, [
        {"name": "r", "role": "reader", "hash": hash_token("reader-tok")},
        {"name": "w", "role": "writer", "hash": hash_token("writer-tok")},
        {"name": "a", "role": "admin", "hash": hash_token("admin-tok")},
    ]


@pytest.fixture
def server_app(tmp_vault, server_tokens, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    from agent_wiki.server.app import create_app
    _tokens_map, token_entries = server_tokens
    cfg = {"bind": "127.0.0.1", "port": 8731, "tokens": token_entries}
    return create_app(tmp_vault, cfg)


@pytest.fixture
def client(server_app):
    from fastapi.testclient import TestClient
    return TestClient(server_app, raise_server_exceptions=False)


@pytest.fixture
def reader_h():
    return {"Authorization": "Bearer reader-tok"}


@pytest.fixture
def writer_h():
    return {"Authorization": "Bearer writer-tok"}


@pytest.fixture
def admin_h():
    return {"Authorization": "Bearer admin-tok"}


@pytest.fixture
def remote_service(server_app):
    # httpx.ASGITransport is async-only; a sync httpx.Client cannot drive it.
    # fastapi's TestClient is a sync httpx.Client that wraps the ASGI app
    # in-process, which is exactly the transport RemoteVaultService needs here.
    from fastapi.testclient import TestClient
    from agent_wiki.remote import RemoteVaultService
    client = TestClient(server_app, base_url="http://test",
                        raise_server_exceptions=False)
    return RemoteVaultService("http://test", "writer-tok", client=client)
