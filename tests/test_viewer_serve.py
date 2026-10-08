"""The viewer leaves the /v1 API and FastAPI's docs paths alone, and `awiki
serve` warns when the viewer is reachable off this host or the API has no
tokens. uvicorn.run is patched out so serve returns instead of listening."""

import pytest
import yaml
from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.registry import VaultEntry
from agent_wiki.server_config import hash_token
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
def with_token(tmp_config):
    """Configure one token so the no-tokens warning stays out of the way."""
    (tmp_config.parent / "server.yaml").write_text(yaml.dump({
        "tokens": [{"name": "r", "role": "reader", "hash": hash_token("tok")}]}))


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.5", "wiki.example.com"])
def test_viewer_on_non_loopback_bind_warns_no_auth(serve, with_token, bind):
    result = serve("--viewer", "--bind", bind)
    assert result.exit_code == 0, result.output
    assert result.output.count(NO_AUTH) == 1


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.8.0.3", "::1", "localhost"])
def test_viewer_on_loopback_bind_does_not_warn(serve, with_token, bind):
    result = serve("--viewer", "--bind", bind)
    assert result.exit_code == 0, result.output
    assert NO_AUTH not in result.output


def test_non_loopback_bind_without_viewer_does_not_warn(serve, with_token):
    result = serve("--bind", "0.0.0.0")
    assert result.exit_code == 0, result.output
    assert NO_AUTH not in result.output


def test_no_tokens_warning_with_viewer_names_the_api_only(serve):
    result = serve("--viewer")
    assert result.exit_code == 0, result.output
    warning = next(line for line in result.output.splitlines()
                   if "no tokens configured" in line)
    assert "API requests will be rejected" in warning
    assert "all requests" not in warning
    assert "viewer" not in warning.lower()
