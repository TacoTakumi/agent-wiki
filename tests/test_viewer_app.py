"""Viewer routes mounted into the server app: switched on by a catalog, served
without a token, and absent (404) when the viewer is off."""

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from agent_wiki.cli import cli
from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.viewer.catalog import build_vault_catalog
from conftest import make_vault


SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731, "tokens": []}


@pytest.fixture
def catalog(tmp_path):
    """Two local vaults that exist, one remote, one local whose path is gone."""
    registry = {
        "main": VaultEntry(name="main", path=make_vault(tmp_path / "main")),
        "work": VaultEntry(name="work", path=make_vault(tmp_path / "work")),
        "team": VaultEntry(name="team", url="https://wiki.example.com",
                           token="tok-1"),
        "gone": VaultEntry(name="gone", path=tmp_path / "gone"),
    }
    return build_vault_catalog(registry)


@pytest.fixture
def viewer(tmp_vault, catalog):
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def no_viewer(tmp_vault):
    app = create_app(tmp_vault, SERVER_CONFIG)
    return TestClient(app, raise_server_exceptions=False)


def test_vault_list_is_html_without_a_token(viewer):
    resp = viewer.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_vault_list_links_local_vaults(viewer):
    html = viewer.get("/").text
    assert 'href="/vaults/main/"' in html
    assert 'href="/vaults/work/"' in html


def test_vault_list_does_not_link_remote_or_missing_vaults(viewer):
    html = viewer.get("/").text
    assert "/vaults/team/" not in html
    assert "/vaults/gone/" not in html


def test_vault_list_counts_skipped_remote_vaults(viewer):
    assert "1 remote vault skipped" in viewer.get("/").text


def test_vault_list_marks_missing_vaults_unavailable(viewer):
    html = viewer.get("/").text
    assert "gone" in html
    assert "unavailable" in html


def test_served_vault_route_answers(viewer):
    assert viewer.get("/vaults/main/").status_code == 200


@pytest.mark.parametrize("name", ["team", "gone", "nosuch"])
def test_remote_missing_and_unknown_vaults_return_404(viewer, name):
    assert viewer.get(f"/vaults/{name}/").status_code == 404


def test_api_still_needs_a_token_with_the_viewer_on(viewer):
    assert viewer.get("/v1/status").status_code == 401


def test_viewer_off_serves_no_viewer_routes(no_viewer):
    assert no_viewer.get("/").status_code == 404
    assert no_viewer.get("/vaults/main/").status_code == 404


def test_viewer_routes_stay_out_of_the_api_schema(tmp_vault, catalog):
    app = create_app(tmp_vault, SERVER_CONFIG, viewer=catalog)
    assert all(p.startswith("/v1/") for p in app.openapi()["paths"])


def test_serve_help_lists_viewer_flag():
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--viewer" in result.output
