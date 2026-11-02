"""The viewer serves the images an imported document keeps under a vault's
raw/assets folder, read-only and under the same read access as the vault's
pages: only image extensions, only files inside raw/assets, and a chapter
page's relative img src lands on that route."""
import re
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from agent_wiki.documents import ingest_document
from agent_wiki.registry import VaultEntry
from agent_wiki.server.app import create_app
from agent_wiki.server.store import open_store
from agent_wiki.viewer.catalog import build_vault_catalog
from conftest import VIEWER_USER, make_vault, signed_in_client
from convert_fixtures import build_epub, image_bytes

SERVER_CONFIG = {"bind": "127.0.0.1", "port": 8731, "tokens": []}
ASSETS = "raw/assets/a-book/OEBPS/images"
CHAPTER = "/vaults/main/research/a-book/01-chapter-one"


def book_vault(path):
    vault = make_vault(path)
    ingest_document(build_epub(path.parent / f"{path.name}-src" / "book.epub"),
                    vault)
    return vault


@pytest.fixture
def vaults(tmp_path):
    """main and personal, each with the fixture EPUB imported."""
    return {"main": book_vault(tmp_path / "main"),
            "personal": book_vault(tmp_path / "personal")}


@pytest.fixture
def app(tmp_path, vaults, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))
    registry = {name: VaultEntry(name=name, path=path)
                for name, path in vaults.items()}
    return create_app(vaults["main"], SERVER_CONFIG,
                      viewer=build_vault_catalog(registry))


@pytest.fixture
def client(app):
    """Signed in as a reader on every vault but personal."""
    client = signed_in_client(app, role="reader")
    with open_store(app.state.store_file) as store:
        store.set_grant(VIEWER_USER, "personal", "none")
    return client


def test_an_extracted_jpg_answers_with_its_bytes(client, vaults):
    resp = client.get(f"/vaults/main/{ASSETS}/a.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == (vaults["main"] / ASSETS / "a.jpg").read_bytes()
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("ext, media_type", [
    ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"), ("JPG", "image/jpeg"),
    ("png", "image/png"), ("gif", "image/gif"), ("webp", "image/webp"),
    ("svg", "image/svg+xml"),
])
def test_each_image_extension_gets_its_content_type(client, vaults, ext,
                                                     media_type):
    (vaults["main"] / ASSETS / f"pic.{ext}").write_bytes(image_bytes("png"))
    resp = client.get(f"/vaults/main/{ASSETS}/pic.{ext}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].split(";")[0] == media_type


def test_an_svg_can_run_no_script_when_opened_directly(client, vaults):
    (vaults["main"] / ASSETS / "x.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script>'
        '</svg>')
    resp = client.get(f"/vaults/main/{ASSETS}/x.svg")
    assert resp.status_code == 200
    policy = resp.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "sandbox" in policy


@pytest.mark.parametrize("path", [
    "raw/assets/a-book.epub",          # the archived original
    "raw/assets/a-book/note.md",       # not an image
    "raw/assets/a-book/OEBPS/images/a.jpg.txt",
    "raw/assets/a-book/OEBPS/images/nosuch.jpg",
    "raw/assets/a-book/OEBPS/images",  # a folder
])
def test_anything_but_an_existing_image_is_404(client, vaults, path):
    (vaults["main"] / "raw/assets/a-book/note.md").write_text("# note\n")
    (vaults["main"] / ASSETS / "a.jpg.txt").write_text("text")
    assert client.get(f"/vaults/main/{path}").status_code == 404


def test_a_markdown_file_under_raw_is_404(client, vaults):
    assert (vaults["main"] / "raw/a-book/01-chapter-one.md").is_file()
    assert client.get(
        "/vaults/main/raw/a-book/01-chapter-one.md").status_code == 404
    assert client.get(
        "/vaults/main/raw/assets/../a-book/01-chapter-one.md"
    ).status_code == 404


@pytest.mark.parametrize("path", [
    "../wiki.yaml",
    "%2e%2e/wiki.yaml",
    "..%2fwiki.yaml",
    "%2e%2e%2f%2e%2e%2fwiki.yaml",
    "a-book/%2e%2e/%2e%2e/%2e%2e/wiki.yaml",
])
def test_traversal_out_of_raw_assets_is_404(client, path):
    resp = client.get(f"/vaults/main/raw/assets/{path}")
    assert resp.status_code == 404
    assert "topics" not in resp.text


def test_traversal_to_an_image_outside_raw_assets_is_404(client, vaults):
    (vaults["main"] / "raw" / "outside.png").write_bytes(image_bytes("png"))
    assert client.get(
        "/vaults/main/raw/assets/%2e%2e/outside.png").status_code == 404


def test_a_symlink_out_of_raw_assets_is_404(client, vaults, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(image_bytes("png"))
    (vaults["main"] / ASSETS / "link.png").symlink_to(secret)
    (vaults["main"] / ASSETS / "yaml.png").symlink_to(
        vaults["main"] / "wiki.yaml")
    assert client.get(f"/vaults/main/{ASSETS}/link.png").status_code == 404
    assert client.get(f"/vaults/main/{ASSETS}/yaml.png").status_code == 404


def test_a_symlink_to_a_non_image_inside_raw_assets_is_404(client, vaults):
    (vaults["main"] / ASSETS / "epub.png").symlink_to(
        vaults["main"] / "raw/assets/a-book.epub")
    assert client.get(f"/vaults/main/{ASSETS}/epub.png").status_code == 404


# --- access: as a page request in the same vault ---------------------------

def test_an_unreadable_vault_answers_as_its_pages_do(client):
    page = client.get("/vaults/personal/research/a-book/01-chapter-one")
    image = client.get(f"/vaults/personal/{ASSETS}/a.jpg")
    assert page.status_code == image.status_code == 404
    assert image.json() == page.json()


def test_without_a_session_it_answers_as_a_page_does(app):
    client = TestClient(app, raise_server_exceptions=False)
    page = client.get("/vaults/personal/research/a-book/01-chapter-one",
                      follow_redirects=False)
    image = client.get(f"/vaults/personal/{ASSETS}/a.jpg",
                       follow_redirects=False)
    assert page.status_code == image.status_code == 303
    assert page.headers["location"].startswith("/signin")
    assert image.headers["location"].startswith("/signin")


def test_an_unknown_vault_is_404(client):
    assert client.get(f"/vaults/nosuch/{ASSETS}/a.jpg").status_code == 404


# --- a rendered page's images resolve to the route -------------------------

@pytest.mark.parametrize("page_path", [
    CHAPTER,                              # chapter 1 shows its figure
    "/vaults/main/research/a-book",       # the parent page shows the cover
])
def test_a_rendered_page_img_src_resolves_to_the_image(client, page_path):
    resp = client.get(page_path)
    assert resp.status_code == 200
    sources = re.findall(r'<img [^>]*src="([^"]+)"', resp.text)
    assert sources
    for src in sources:
        url = urljoin(f"http://testserver{page_path}", src)
        assert url.startswith("http://testserver/vaults/main/raw/assets/")
        image = client.get(url)
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/")
