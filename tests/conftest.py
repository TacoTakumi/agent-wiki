import os
import pytest
import yaml
import json as _json


@pytest.fixture(autouse=True)
def _isolate_awiki_config(tmp_path_factory, monkeypatch):
    """Guard: no test may read or write the developer's real user config.

    Point AGENT_WIKI_CONFIG_DIR at a throwaway dir for EVERY test. Tests that need
    their own config dir set it themselves (tmp_config / _setup_vault); their
    monkeypatch.setenv runs after this and wins. The danger this closes: a test
    that sets nothing used to fall through to ~/.config/agent-wiki, and any config
    write on that path (`awiki init`, stale-config repair) would clobber a live
    developer's vault_path — breaking their other sessions. This makes the real
    config unreachable from the suite. To run awiki against a scratch vault by
    hand, prefer `AWIKI_VAULT=<dir>` (a vault override that writes no config)."""
    monkeypatch.setenv(
        "AGENT_WIKI_CONFIG_DIR", str(tmp_path_factory.mktemp("awiki-config")))


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


def make_vault(vault, name="Test Wiki"):
    """Create a vault directory with wiki.yaml and the standard structure."""
    vault.mkdir(parents=True, exist_ok=True)

    config = {
        "vault": {"name": name, "version": 1},
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
def tmp_vault(tmp_path):
    """Create a temporary vault directory with wiki.yaml and standard structure."""
    return make_vault(tmp_path / "vault")


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
def two_vault_config(tmp_path, monkeypatch):
    """Two local vaults registered under a vaults: map in an isolated config dir.

    Returns the config file path; the vaults live at <tmp>/work-vault and
    <tmp>/personal-vault under the names 'work' and 'personal'."""
    work = make_vault(tmp_path / "work-vault", name="Work Wiki")
    personal = make_vault(tmp_path / "personal-vault", name="Personal Wiki")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(yaml.dump({
        "vaults": {
            "work": {"path": str(work)},
            "personal": {"path": str(personal)},
        }
    }))
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


# The API tests' bearer secrets and the role each acts with: one user,
# 'tester', holds admin on every vault, and each token's scope narrows it.
SERVER_TOKEN_ROLES = {
    "reader-tok": "reader",
    "writer-tok": "writer",
    "admin-tok": "admin",
}
SERVER_TOKEN_USER = "tester"


def seed_server_tokens(store_file=None):
    """Put the 'tester' user and the API tests' tokens in the user store
    (default: the store in the current config dir). Returns {secret: role}."""
    from agent_wiki.server import accounts
    from agent_wiki.server.store import Token, open_store
    with open_store(store_file) as store:
        accounts.add_user(store, SERVER_TOKEN_USER, None)
        accounts.set_grant(store, SERVER_TOKEN_USER, "*", "admin")
        for secret, role in SERVER_TOKEN_ROLES.items():
            store.add_token(Token(
                owner=SERVER_TOKEN_USER, name=role,
                hash=accounts.hash_secret(secret), last4=secret[-4:],
                scope={"*": role}, created="2026-01-01T00:00:00Z"))
    return dict(SERVER_TOKEN_ROLES)


@pytest.fixture
def server_tokens():
    """The API tests' tokens, in the user store of the current config dir:
    {secret: role}."""
    return seed_server_tokens()


@pytest.fixture(params=[False, True], ids=["viewer-off", "viewer-on"])
def server_app(request, tmp_vault, server_tokens, tmp_path, monkeypatch):
    """The API app, built once without and once with the HTML viewer mounted:
    every API test runs against both, proving the viewer leaves /v1 unchanged."""
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    from agent_wiki.server.app import create_app
    cfg = {"bind": "127.0.0.1", "port": 8731}
    catalog = None
    if request.param:
        from agent_wiki.registry import VaultEntry
        from agent_wiki.viewer.catalog import build_vault_catalog
        catalog = build_vault_catalog(
            {"main": VaultEntry(name="main", path=tmp_vault)})
    return create_app(tmp_vault, cfg, viewer=catalog)


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


# The user the viewer tests browse as: a reader on every vault, signed in by
# a session opened straight in the store, so no test pays for scrypt.
VIEWER_USER = "viewer"


def sign_in(client, user=VIEWER_USER, role="reader"):
    """Sign `client` (a TestClient on an app built with the viewer) in as
    `user`: the user is added to the app's store with no password and
    `role` on every vault when missing, and a session's cookie is set on
    the client. Returns the session secret."""
    from agent_wiki.server import accounts
    from agent_wiki.server.store import User, open_store
    from agent_wiki.viewer.session import SESSION_COOKIE
    with open_store(client.app.state.store_file) as store:
        if store.get_user(user) is None:
            store.add_user(User(name=user, password_hash=None,
                                created="2026-01-01T00:00:00Z"))
            store.set_grant(user, "*", role)
        secret = accounts.open_session(store, user, "password")
    client.cookies.set(SESSION_COOKIE, secret)
    return secret


def signed_in_client(app, user=VIEWER_USER, role="reader", **kwargs):
    """A TestClient on `app`, signed in as `user` (see sign_in). Keyword
    arguments go to TestClient; server exceptions become 500s unless
    raise_server_exceptions says otherwise."""
    from fastapi.testclient import TestClient
    kwargs.setdefault("raise_server_exceptions", False)
    client = TestClient(app, **kwargs)
    sign_in(client, user, role)
    return client
