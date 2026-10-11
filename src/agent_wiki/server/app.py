"""FastAPI app factory. Wraps a LocalVaultService and maps core exceptions
to HTTP status codes (symmetric with RemoteVaultService)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agent_wiki.ingest import UnchangedURLSkip
from agent_wiki.service import LocalVaultService
from agent_wiki.server.auth import make_require
from agent_wiki.server.routes import build_router
from agent_wiki.server.store import store_path
from agent_wiki.server_config import proxy_auth
from agent_wiki.viewer.catalog import served_vault_name

# The minimum access of every route the app serves, keyed by (method, path):
# "public" answers anyone, "viewer" needs a signed-in browser session, and
# "reader", "writer" and "admin" are the bearer-token role a /v1 route asks
# for. The table fails closed: create_app refuses to build an app with a
# route the table does not name, and the tests check each entry against
# what the route really does.
PUBLIC, VIEWER = "public", "viewer"
ROUTE_ACCESS: dict[tuple[str, str], str] = {
    # The /v1 API: bearer tokens only, never a browser session.
    ("GET", "/v1/search"): "reader",
    ("GET", "/v1/pages/{path:path}"): "reader",
    ("GET", "/v1/status"): "reader",
    ("GET", "/v1/log"): "reader",
    ("GET", "/v1/lint"): "reader",
    ("POST", "/v1/context"): "reader",
    ("POST", "/v1/ingest"): "writer",
    ("POST", "/v1/ingest_url"): "writer",
    ("POST", "/v1/reingest"): "writer",
    ("POST", "/v1/conversations"): "writer",
    ("POST", "/v1/index"): "writer",
    ("POST", "/v1/sync"): "writer",
    ("POST", "/v1/adapt"): "writer",
    ("POST", "/v1/doctor"): "admin",
    # Sign-in and the favicons answer without a session.
    ("GET", "/signin"): PUBLIC,
    ("POST", "/signin"): PUBLIC,
    ("GET", "/favicon.svg"): PUBLIC,
    ("GET", "/favicon.ico"): PUBLIC,
    ("GET", "/apple-touch-icon.png"): PUBLIC,
    # Everything else the viewer serves needs a signed-in session.
    ("POST", "/signout"): VIEWER,
    ("GET", "/user"): VIEWER,
    ("POST", "/user"): VIEWER,
    ("POST", "/theme"): VIEWER,
    ("GET", "/"): VIEWER,
    ("GET", "/search"): VIEWER,
    ("GET", "/vaults/{name}/"): VIEWER,
    ("GET", "/vaults/{name}/{topic}/"): VIEWER,
    ("GET", "/vaults/{name}/-/tags/{tag:path}"): VIEWER,
    ("GET", "/vaults/{name}/{path:path}"): VIEWER,
}


def app_routes(app) -> set[tuple[str, str]]:
    """Every (method, path) the app serves, routers included. A HEAD that
    rides along with a GET is not counted on its own. A route of a kind
    this cannot read raises, so nothing slips past the table unseen."""
    found: set[tuple[str, str]] = set()

    def walk(routes, prefix: str) -> None:
        for route in routes:
            # FastAPI keeps an included router as one entry that points at
            # the router it came from; older versions copy its routes in.
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(original.routes, prefix + route.include_context.prefix)
                continue
            path = getattr(route, "path", None)
            if not isinstance(path, str):
                raise TypeError(f"cannot read the path of route {route!r}")
            methods = getattr(route, "methods", None)
            if methods is None:
                # A mount or a websocket answers whatever it is sent.
                found.add(("*", prefix + path))
                continue
            methods = set(methods)
            if "GET" in methods:
                methods.discard("HEAD")
            for method in methods:
                found.add((method, prefix + path))

    walk(app.router.routes, "")
    return found


def undeclared_routes(app) -> list[tuple[str, str]]:
    """The app's routes that ROUTE_ACCESS does not name, sorted."""
    return sorted(app_routes(app) - ROUTE_ACCESS.keys())


def create_app(vault_path: Path, server_config: dict, viewer=None,
               vault_name: str | None = None) -> FastAPI:
    """Build the API app for one vault. `viewer` is a VaultCatalog: given one,
    the HTML viewer routes are mounted too; without it they do not exist.

    Bearer tokens are checked against the user store in the config dir (as
    it is when the app is built), with grants matched against `vault_name`:
    by default the vault's registry name, or 'local' outside the registry.
    The viewer's browser sessions live in the same store. `server_config`
    is server.yaml's settings; its tokens are never read."""
    # No /docs, /redoc or /openapi.json: the API is documented in the
    # project, not served by the app.
    app = FastAPI(title="Agent Wiki", version="1", docs_url=None,
                  redoc_url=None, openapi_url=None)
    svc = LocalVaultService(vault_path)
    if vault_name is None:
        vault_name = served_vault_name(vault_path)
    store_file = store_path()
    app.state.store_file = store_file
    # Who may name a user in a header (the viewer only, never /v1), and
    # whose X-Forwarded-Proto counts. A bad setting stops the app here.
    app.state.proxy_auth = proxy_auth(server_config)
    require = make_require(vault_name, store_file)

    @app.exception_handler(FileNotFoundError)
    async def _not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(FileExistsError)
    async def _conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(UnchangedURLSkip)
    async def _unchanged_url(request, exc):
        # Not an error: a re-ingest of an unchanged URL. 200 + sentinel so the
        # remote client re-raises UnchangedURLSkip (symmetric with local skip).
        return JSONResponse(status_code=200, content={"unchanged": True, "url": exc.url})

    @app.exception_handler(ValueError)
    async def _value_error(request, exc):
        msg = str(exc)
        if "cannot display binary file" in msg:
            return JSONResponse(status_code=415, content={"detail": msg})
        return JSONResponse(status_code=400, content={"detail": msg})

    @app.exception_handler(TimeoutError)
    async def _timeout(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _generic(request, exc):
        # No internals leaked. (HTTPException is handled by FastAPI itself.)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    app.include_router(build_router(svc, require))
    if viewer is not None:
        from agent_wiki.viewer.auth_routes import (build_auth_router,
                                                   refuse_unknown_proxy_user,
                                                   sign_in_redirect)
        from agent_wiki.viewer.routes import build_viewer_router
        from agent_wiki.viewer.session import (ProxyUserUnknown,
                                               SessionCookieMiddleware,
                                               SignInRequired)
        app.add_exception_handler(SignInRequired, sign_in_redirect)
        app.add_exception_handler(ProxyUserUnknown, refuse_unknown_proxy_user)
        app.add_middleware(SessionCookieMiddleware)
        app.include_router(build_auth_router(store_file, viewer))
        app.include_router(build_viewer_router(viewer))
    undeclared = undeclared_routes(app)
    if undeclared:
        raise RuntimeError(
            "routes with no declared access: "
            + ", ".join(f"{method} {path}" for method, path in undeclared))
    return app
