"""HTML routes for the viewer. Built against an injected VaultCatalog so the
same router works for any set of vaults (and tests never read the real
config). The routes need no token: the viewer is read-only, and the /v1 API
keeps its own auth."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from agent_wiki.viewer.catalog import VaultCatalog


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

    return r
