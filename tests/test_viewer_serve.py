"""The viewer leaves the /v1 API and FastAPI's docs paths alone, and `awiki
serve` no longer warns when the viewer is reachable off this host: every
viewer page asks for a sign-in. uvicorn.run is patched out so serve returns
instead of listening."""

import pytest
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.registry import VaultEntry
from agent_wiki.viewer.catalog import build_vault_catalog
from agent_wiki.viewer.routes import build_viewer_router


NO_AUTH = "readable without authentication"
API_PREFIXES = ("/v1", "/docs", "/redoc", "/openapi.json")


def test_no_viewer_route_uses_api_or_docs_paths(tmp_vault):
    # The app mounts the viewer router with no prefix, so its own route table
    # holds the exact paths the viewer serves.
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=tmp_vault)})
    viewer_paths = [r.path for r in build_viewer_router(catalog).routes]
    assert viewer_paths
    assert not [p for p in viewer_paths if p.startswith(API_PREFIXES)]


@pytest.fixture
def serve(tmp_config, monkeypatch):
    """Run `awiki serve` with uvicorn.run stubbed; returns the CLI result."""
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    def run(*args):
        return CliRunner().invoke(cli, ["serve", *args])
    return run


@pytest.fixture
def with_user():
    """One user with a grant on every vault, so serve has no account warning."""
    from agent_wiki.server import accounts
    from agent_wiki.server.store import open_store
    with open_store() as store:
        accounts.add_user(store, "alice", None)
        accounts.set_grant(store, "alice", "*", "reader")


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.5", "wiki.example.com",
                                  "127.0.0.1", "::1", "localhost"])
def test_viewer_on_any_bind_with_users_prints_no_warning(serve, with_user, bind):
    result = serve("--viewer", "--bind", bind)
    assert result.exit_code == 0, result.output
    assert NO_AUTH not in result.output
    assert "loopback" not in result.output
    assert "WARNING" not in result.output


def test_non_loopback_bind_without_viewer_does_not_warn(serve, with_user):
    result = serve("--bind", "0.0.0.0")
    assert result.exit_code == 0, result.output
    assert NO_AUTH not in result.output
    assert "WARNING" not in result.output


def test_serve_with_viewer_prints_no_token_warning(serve):
    result = serve("--viewer")
    assert result.exit_code == 0, result.output
    assert "no tokens configured" not in result.output
    assert "awiki token add" not in result.output
