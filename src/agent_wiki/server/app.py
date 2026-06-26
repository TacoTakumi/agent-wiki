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


def create_app(vault_path: Path, server_config: dict) -> FastAPI:
    app = FastAPI(title="Agent Wiki", version="1")
    svc = LocalVaultService(vault_path)
    require = make_require(server_config)

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
    return app
