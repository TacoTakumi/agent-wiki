"""Viewer pages are sent with a Content-Security-Policy: no script, no
external CSS, the inline style block allowed by a per-response nonce and the
table alignment attributes by hash. The /v1 API is not a page and gets none."""
from __future__ import annotations

import base64
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.viewer.catalog import build_vault_catalog

from conftest import make_vault, signed_in_client

TABLE = "| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |\n"


@pytest.fixture
def app(tmp_path):
    vault = make_vault(tmp_path / "main")
    (vault / "research" / "grid.md").write_text(
        "---\ntitle: Grid\ntopic: research\n---\n\n# Grid\n\n" + TABLE)
    catalog = build_vault_catalog({"main": VaultEntry(name="main", path=vault)})
    return create_app(vault, {"bind": "127.0.0.1", "port": 8731},
                      viewer=catalog)


def csp(response):
    return response.headers["content-security-policy"]


def style_nonce(html):
    return re.search(r'<style nonce="([^"]+)">', html).group(1)


def sha(value):
    return "'sha256-" + base64.b64encode(
        hashlib.sha256(value.encode()).digest()).decode() + "'"


@pytest.mark.parametrize("url", ["/", "/vaults/main/research/grid",
                                 "/search?q=grid", "/user"])
def test_signed_in_pages_carry_the_policy_and_its_nonce(app, url):
    response = signed_in_client(app).get(url)
    assert response.status_code == 200
    policy = csp(response)
    assert "default-src 'none'" in policy
    assert "base-uri 'none'" in policy
    assert "form-action 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "img-src 'self' data: https:" in policy
    assert f"'nonce-{style_nonce(response.text)}'" in policy
    assert "'unsafe-hashes'" in policy
    for value in ("text-align:left", "text-align:center", "text-align:right"):
        assert sha(value) in policy
    assert "script-src" not in policy
    assert "'unsafe-inline'" not in policy


def test_the_sign_in_page_carries_the_policy_too(app):
    response = TestClient(app).get("/signin")
    assert f"'nonce-{style_nonce(response.text)}'" in csp(response)


def test_each_response_gets_its_own_nonce(app):
    client = signed_in_client(app)
    first = style_nonce(client.get("/").text)
    second = style_nonce(client.get("/").text)
    assert first != second


def test_table_alignment_attributes_are_the_hashed_values(app):
    html = signed_in_client(app).get("/vaults/main/research/grid").text
    styles = set(re.findall(r'style="([^"]*)"', html))
    assert styles == {"text-align:left", "text-align:center",
                      "text-align:right"}


def test_the_api_is_sent_no_policy(app):
    response = TestClient(app).get("/v1/status")
    assert "content-security-policy" not in response.headers
