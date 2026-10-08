"""HTML routes for the viewer. Built against an injected VaultCatalog so the
same router works for any set of vaults (and tests never read the real
config). The routes need no token: the viewer is read-only, and the /v1 API
keeps its own auth."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from agent_wiki.page import parse_page
from agent_wiki.viewer.catalog import VaultCatalog, index_vault, resolve_page
from agent_wiki.viewer.render import render_markdown


templates = Environment(
    loader=PackageLoader("agent_wiki.viewer", "templates"),
    autoescape=select_autoescape(["html"]),
)


def render(template: str, **context) -> HTMLResponse:
    """Render a template from the viewer's templates folder as an HTML response."""
    return HTMLResponse(templates.get_template(template).render(**context))


def served_vault(catalog: VaultCatalog, name: str):
    """The path of a served vault, or 404: remote, unavailable and unknown
    names are all simply not served."""
    if name not in catalog.vaults:
        raise HTTPException(status_code=404, detail="vault not found")
    return catalog.vaults[name]


def page_url(name: str, url_path: str) -> str:
    """The viewer URL of a page in a served vault, each segment quoted."""
    return f"/vaults/{quote(name, safe='')}/{quote(url_path)}"


def source_entries(sources) -> list[tuple[str, bool]]:
    """Each sources entry as (text, is_link): only http and https URLs are
    links; raw/ paths and anything else stay plain text."""
    if not isinstance(sources, list):
        return []
    return [(str(s), str(s).startswith(("http://", "https://")))
            for s in sources]


def build_viewer_router(catalog: VaultCatalog) -> APIRouter:
    # Kept out of the OpenAPI schema: the schema documents the /v1 API only.
    r = APIRouter(include_in_schema=False)

    @r.get("/", response_class=HTMLResponse)
    def vault_list():
        return render("vaults.html", title="Vaults", catalog=catalog)

    @r.get("/vaults/{name}/", response_class=HTMLResponse)
    def vault_home(name: str):
        served_vault(catalog, name)
        return render("base.html", title=name)

    # Registered after the vault home, so /vaults/<name>/ stays that route.
    @r.get("/vaults/{name}/{path:path}", response_class=HTMLResponse)
    def page_view(name: str, path: str):
        vault_dir = served_vault(catalog, name)
        page_file = resolve_page(vault_dir, path)
        if page_file is None:
            raise HTTPException(status_code=404, detail="page not found")
        index = index_vault(vault_dir)
        topic = path.split("/", 1)[0]
        record = next((rec for rec in index.topics.get(topic, ())
                       if rec.url_path == path), None)
        # A .md file without frontmatter is not a page: the listings leave it
        # out, so the page view does too.
        if record is None:
            raise HTTPException(status_code=404, detail="page not found")
        page = parse_page(page_file)
        links = {title: page_url(name, url_path)
                 for title, url_path in index.titles.items()}
        return render(
            "page.html",
            title=record.title,
            page=record,
            body=render_markdown(page["body"], links),
            sources=source_entries(page["meta"].get("sources")),
        )

    return r
