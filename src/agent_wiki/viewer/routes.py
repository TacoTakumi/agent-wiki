"""HTML routes for the viewer. Built against an injected VaultCatalog so the
same router works for any set of vaults (and tests never read the real
config). The routes need no token: the viewer is read-only, and the /v1 API
keeps its own auth."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import ChoiceLoader, Environment, PackageLoader, select_autoescape

from agent_wiki.multisearch import merged_search
from agent_wiki.page import parse_page
from agent_wiki.registry import VaultEntry
from agent_wiki.viewer.catalog import (VaultCatalog, index_vault, is_utf8,
                                       resolve_page)
from agent_wiki.viewer.render import render_markdown


# The static folder is on the search path so base.html can include the
# stylesheets inline: pages load no external CSS.
templates = Environment(
    loader=ChoiceLoader([PackageLoader("agent_wiki.viewer", "templates"),
                         PackageLoader("agent_wiki.viewer", "static")]),
    autoescape=select_autoescape(["html"]),
)


# The header theme switch: the choice lives in this cookie for a year.
THEME_COOKIE = "awiki_theme"
THEMES = ("auto", "light", "dark")
THEME_MAX_AGE = 31536000


def current_theme(request: Request) -> str:
    """The theme from the cookie, or auto when it is missing or not valid."""
    value = request.cookies.get(THEME_COOKIE)
    return value if value in THEMES else "auto"


def request_target(request: Request) -> str:
    """The requested path and query as the client sent them, still quoted, so
    the theme switch can send the browser back to the same URL."""
    raw = request.scope.get("raw_path")
    path = raw.decode("latin-1") if raw else quote(request.url.path)
    query = request.url.query
    return f"{path}?{query}" if query else path


def same_site_path(target: str | None) -> str:
    """`target` when it is a path on this site, else /. A path starts with a
    single / and holds no backslash or control character, so the browser can
    not read it as another host."""
    if (not target or not target.startswith("/") or target.startswith("//")
            or "\\" in target
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in target)):
        return "/"
    return target


def render(request: Request, template: str, *, header_vault: str | None = None,
           header_search: bool = True, **context) -> HTMLResponse:
    """Render a template from the viewer's templates folder as an HTML
    response. Every page gets the theme context here; the page depends on the
    theme cookie, so the response varies on Cookie.

    The header context is built here too: a vault view passes `header_vault`,
    so the header links that vault's home and searches only that vault; any
    other page gets a search over all vaults unless `header_search` is False."""
    header_vault_url = (f"/vaults/{quote(header_vault, safe='')}/"
                        if header_vault else None)
    html = templates.get_template(template).render(
        theme=current_theme(request), themes=THEMES,
        theme_next=request_target(request), header_vault=header_vault,
        header_vault_url=header_vault_url, header_search=header_search,
        **context)
    return HTMLResponse(html, headers={"Vary": "Cookie"})


def served_vault(catalog: VaultCatalog, name: str):
    """The path of a served vault, or 404: remote, unavailable and unknown
    names are all simply not served."""
    if name not in catalog.vaults:
        raise HTTPException(status_code=404, detail="vault not found")
    return catalog.vaults[name]


def page_url(name: str, url_path: str) -> str:
    """The viewer URL of a page in a served vault, each segment quoted."""
    return f"/vaults/{quote(name, safe='')}/{quote(url_path)}"


def tag_url(name: str, tag: str) -> str:
    """The viewer URL of a tag page. The /-/ segment keeps it clear of any
    topic folder; the tag is quoted whole, so spaces and & round-trip."""
    return f"/vaults/{quote(name, safe='')}/-/tags/{quote(tag, safe='')}"


def source_entries(sources) -> list[tuple[str, bool]]:
    """Each sources entry as (text, is_link): only http and https URLs are
    links; raw/ paths and anything else stay plain text."""
    if not isinstance(sources, list):
        return []
    return [(str(s), str(s).startswith(("http://", "https://")))
            for s in sources]


# How many pages the vault home lists, and how many one listing screen shows.
RECENT_PAGES = 20
SCREEN_SIZE = 50


def by_recency(records) -> list:
    """Pages newest first: by updated, then created, then title. Dates are
    ISO strings, so they compare as text; an empty date sorts after every
    real one, so pages with neither date come last."""
    by_title = sorted(records, key=lambda rec: rec.title)
    # A reverse sort is still stable, so equal dates keep title order.
    return sorted(by_title, key=lambda rec: (rec.updated, rec.created),
                  reverse=True)


def screen(records: list, page: int) -> tuple[list, int]:
    """One listing screen (1-based) of `records` and the last screen number,
    or 404 past either end. An empty listing has one empty screen."""
    last = max(1, -(-len(records) // SCREEN_SIZE))
    if not 1 <= page <= last:
        raise HTTPException(status_code=404, detail="page out of range")
    start = (page - 1) * SCREEN_SIZE
    return records[start:start + SCREEN_SIZE], last


# The CLI search's caps (all-terms and partial tiers), and how much of each
# hit's matching lines one result shows.
SEARCH_LIMIT = 20
SEARCH_PARTIAL_LIMIT = 5
RESULT_LINES = 3
LINE_CHARS = 200


def search_hit(hit: dict) -> dict:
    """One merged search hit as the results list shows it: its vault, title,
    page view URL, coverage, and its first matching lines, each cut short."""
    name = hit["vault"]
    rel = hit["path"][len(name) + 1:]
    url_path = rel[:-3] if rel.endswith(".md") else rel
    return {
        "vault": name,
        "title": hit["title"],
        "url": page_url(name, url_path),
        "coverage": hit["coverage"],
        "term_count": hit["term_count"],
        "lines": [line[:LINE_CHARS] for line in hit["matches"][:RESULT_LINES]],
    }


def build_viewer_router(catalog: VaultCatalog) -> APIRouter:
    # Kept out of the OpenAPI schema: the schema documents the /v1 API only.
    r = APIRouter(include_in_schema=False)

    @r.post("/theme")
    def set_theme(theme: str | None = Form(default=None),
                  next: str | None = Form(default=None)):
        if theme not in THEMES:
            raise HTTPException(status_code=400, detail="unknown theme")
        resp = RedirectResponse(same_site_path(next), status_code=303)
        resp.set_cookie(THEME_COOKIE, theme, max_age=THEME_MAX_AGE, path="/",
                        samesite="lax", httponly=True)
        return resp

    @r.get("/", response_class=HTMLResponse)
    def vault_list(request: Request):
        return render(request, "vaults.html", title="Vaults", catalog=catalog)

    @r.get("/vaults/{name}/", response_class=HTMLResponse)
    def vault_home(request: Request, name: str):
        index = index_vault(served_vault(catalog, name))
        every_page = [rec for recs in index.topics.values() for rec in recs]
        return render(
            request,
            "vault.html",
            title=name,
            name=name,
            header_vault=name,
            topics=index.topics,
            recent=by_recency(every_page)[:RECENT_PAGES],
            page_url=page_url,
        )

    @r.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "",
               vault: list[str] = Query(default=[])):
        # Unknown names are dropped; none left means every vault, as when
        # no vault parameter was given.
        selected = [n for n in catalog.vaults if n in vault] or list(
            catalog.vaults)
        query = q.strip()
        out, skipped = None, []
        if query:
            entries = {n: VaultEntry(name=n, path=catalog.vaults[n])
                       for n in selected}
            out, skipped = merged_search(entries, query,
                                         limit=SEARCH_LIMIT,
                                         partial_limit=SEARCH_PARTIAL_LIMIT)
            # A hit whose file name is not UTF-8 has no page view URL, as
            # the listings leave that page out, so it is dropped.
            out = dict(out,
                       all=[search_hit(h) for h in out["all"]
                            if is_utf8(h["path"])],
                       partial=[search_hit(h) for h in out["partial"]
                                if is_utf8(h["path"])])
        return render(
            request,
            "search.html",
            header_search=False,
            title=f"{query} - Search" if query else "Search",
            q=query,
            vaults=list(catalog.vaults),
            selected=selected,
            out=out,
            skipped=skipped,
        )

    # Registered before the page view, which would otherwise catch
    # /vaults/<name>/<topic>/ as a page path.
    @r.get("/vaults/{name}/{topic}/", response_class=HTMLResponse)
    def topic_view(request: Request, name: str, topic: str, page: int = 1):
        index = index_vault(served_vault(catalog, name))
        if topic not in index.topics:
            raise HTTPException(status_code=404, detail="topic not found")
        pages, last = screen(by_recency(index.topics[topic]), page)
        return render(
            request,
            "topic.html",
            title=f"{topic} - {name}",
            name=name,
            header_vault=name,
            topic=topic,
            pages=pages,
            page=page,
            last_page=last,
            page_url=page_url,
        )

    # Registered before the page view, which would 404 it: "-" is not a
    # topic. A path parameter, so a tag holding a slash still reaches here.
    @r.get("/vaults/{name}/-/tags/{tag:path}", response_class=HTMLResponse)
    def tag_view(request: Request, name: str, tag: str, page: int = 1):
        index = index_vault(served_vault(catalog, name))
        wanted = tag.casefold()
        tagged = [rec for recs in index.topics.values() for rec in recs
                  if any(t.casefold() == wanted for t in rec.tags)]
        pages, last = screen(by_recency(tagged), page)
        return render(
            request,
            "tag.html",
            title=f"{tag} - {name}",
            name=name,
            header_vault=name,
            tag=tag,
            pages=pages,
            page=page,
            last_page=last,
            page_url=page_url,
        )

    # Registered after the vault home, topic and tag routes, so those keep
    # their URLs.
    @r.get("/vaults/{name}/{path:path}", response_class=HTMLResponse)
    def page_view(request: Request, name: str, path: str):
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
            request,
            "page.html",
            title=record.title,
            name=name,
            header_vault=name,
            page=record,
            tag_url=tag_url,
            body=render_markdown(page["body"], links),
            sources=source_entries(page["meta"].get("sources")),
        )

    return r
