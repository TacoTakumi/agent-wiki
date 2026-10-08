"""HTML routes for the viewer. Built against an injected VaultCatalog so the
same router works for any set of vaults (and tests never read the real
config). The routes need no token: the viewer is read-only, and the /v1 API
keeps its own auth."""
from __future__ import annotations

import sys
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import ChoiceLoader, Environment, PackageLoader, select_autoescape

from agent_wiki.config import backend_for_entry
from agent_wiki.page import parse_page
from agent_wiki.registry import VaultEntry
from agent_wiki.viewer.catalog import (VaultCatalog, index_vault,
                                       resolve_page)
from agent_wiki.viewer.highlight import highlight
from agent_wiki.viewer.render import render_markdown


# The static folder is on the search path so base.html can include the
# stylesheets inline: pages load no external CSS.
templates = Environment(
    loader=ChoiceLoader([PackageLoader("agent_wiki.viewer", "templates"),
                         PackageLoader("agent_wiki.viewer", "static")]),
    autoescape=select_autoescape(["html"]),
)
# {{ text | highlight(query) }} marks the query terms; it escapes the text
# itself and returns Markup, so autoescape leaves the result alone.
templates.filters["highlight"] = highlight


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
           header_search: bool = True, tabs: list | None = None,
           crumbs: list | None = None, **context) -> HTMLResponse:
    """Render a template from the viewer's templates folder as an HTML
    response. Every page gets the theme context here; the page depends on the
    theme cookie, so the response varies on Cookie.

    The header context is built here too: a vault view passes `header_vault`,
    so the header links that vault's home and searches only that vault; any
    other page gets a search over all vaults unless `header_search` is False.
    A vault view also passes its topic `tabs` and `crumbs` (see vault_nav);
    base.html shows each only when it is given."""
    header_vault_url = vault_url(header_vault) if header_vault else None
    html = templates.get_template(template).render(
        theme=current_theme(request), themes=THEMES,
        theme_next=request_target(request), header_vault=header_vault,
        header_vault_url=header_vault_url, header_search=header_search,
        tabs=tabs, crumbs=crumbs, **context)
    return HTMLResponse(html, headers={"Vary": "Cookie"})


def vault_url(name: str) -> str:
    """The viewer URL of a served vault's home."""
    return f"/vaults/{quote(name, safe='')}/"


def topic_url(name: str, topic: str) -> str:
    """The viewer URL of a topic listing, the topic quoted whole."""
    return f"{vault_url(name)}{quote(topic, safe='')}/"


# The tab that marks the vault home, as `current` for vault_nav.
OVERVIEW = object()


def vault_nav(name: str, index, current, crumbs: list) -> dict:
    """The topic tabs and breadcrumb of a vault view, as render() keywords.

    The tabs are Overview (the vault home), then each topic of `index` in
    wiki.yaml order with its page count, the same records its listing
    serves. `current` is OVERVIEW, a topic name, or None for no current tab.
    `crumbs` is a list of (label, url or None); the last part is the current
    one unless it has a URL, as on a page view, where the title follows."""
    tabs = [{"label": "Overview", "count": None, "url": vault_url(name),
             "current": current is OVERVIEW}]
    tabs += [{"label": topic, "count": len(records),
              "url": topic_url(name, topic), "current": topic == current}
             for topic, records in index.topics.items()]
    parts = [{"label": label, "url": url,
              "current": url is None and n == len(crumbs) - 1}
             for n, (label, url) in enumerate(crumbs)]
    return {"tabs": tabs, "crumbs": parts}


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


def served_search(vaults: dict, query: str) -> tuple[dict, list]:
    """Search each vault ({name: path}) and keep only the hits the page view
    serves: pages in the vault's index, so a hit outside the wiki.yaml topic
    folders, without frontmatter or with a file name that is not UTF-8 is
    left out. Each vault is searched in full before that cut, so left-out
    hits neither crowd served ones out of the tiers nor count in the total.
    The tiers are then ranked across vaults as merged_search ranks them and
    capped at the CLI search's limits.

    Returns (result, skipped) in merged_search's shape, plus "all_total",
    the all-terms hit count before the cap: every hit path is
    vault-qualified with its vault name under "vault", and skipped lists
    (vault name, reason) for each vault whose search raised."""
    all_pool, partial_pool, skipped = [], [], []
    for name in sorted(vaults):
        try:
            backend = backend_for_entry(VaultEntry(name=name,
                                                   path=vaults[name]))
            out = backend.search(query, limit=sys.maxsize,
                                 partial_limit=sys.maxsize)
            index = index_vault(vaults[name])
        except Exception as e:
            skipped.append((name, str(e)))
            continue
        served = {rec.url_path for recs in index.topics.values()
                  for rec in recs}
        for hits, pool in ((out["all"], all_pool),
                           (out["partial"], partial_pool)):
            pool.extend(dict(hit, vault=name, path=f"{name}:{hit['path']}")
                        for hit in hits
                        if hit["path"].endswith(".md")
                        and hit["path"][:-3] in served)

    def rank_key(hit):
        return (-hit["coverage"], -len(hit["matches"]), hit["title"])

    all_pool.sort(key=rank_key)
    partial_pool.sort(key=rank_key)
    shown_all = all_pool[:SEARCH_LIMIT]
    shown_partial = partial_pool[:SEARCH_PARTIAL_LIMIT]
    total = len(all_pool) + len(partial_pool)
    shown = len(shown_all) + len(shown_partial)
    return {
        "all": shown_all,
        "partial": shown_partial,
        "all_total": len(all_pool),
        "total": total,
        "shown": shown,
        "truncated": shown < total,
    }, skipped


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
        # The topic count is the tab row's: every wiki.yaml topic the index
        # lists (its folder exists), empty ones too. The newest date is the
        # latest date a row shows (updated, else created).
        return render(
            request,
            "vault.html",
            title=name,
            name=name,
            header_vault=name,
            recent=by_recency(every_page)[:RECENT_PAGES],
            page_count=len(every_page),
            topic_count=len(index.topics),
            newest=max((rec.updated or rec.created for rec in every_page),
                       default=""),
            page_url=page_url,
            **vault_nav(name, index, OVERVIEW, [("Vaults", "/"),
                                                (name, None)]),
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
            out, skipped = served_search(
                {n: catalog.vaults[n] for n in selected}, query)
            out = dict(out,
                       all=[search_hit(h) for h in out["all"]],
                       partial=[search_hit(h) for h in out["partial"]])
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
            **vault_nav(name, index, topic, [(name, vault_url(name)),
                                             (topic, None)]),
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
            # There is no tags index view, so 'tags' links nowhere.
            **vault_nav(name, index, None, [(name, vault_url(name)),
                                            ("tags", None), (tag, None)]),
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
            topic_url=topic_url,
            body=render_markdown(page["body"], links, title=record.title),
            sources=source_entries(page["meta"].get("sources")),
            **vault_nav(name, index, topic,
                        [(name, vault_url(name)),
                         (topic, topic_url(name, topic))]),
        )

    return r
