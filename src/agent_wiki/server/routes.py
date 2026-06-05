"""HTTP routes. Each handler calls a VaultService method and serializes.
Built against an injected service + auth dependency factory so the same
router works for any vault/config (and is trivially testable)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from agent_wiki.server.schemas import ContextRequest


def build_router(svc, require) -> APIRouter:
    r = APIRouter(prefix="/v1")

    @r.get("/search")
    def search(q: str, topic: str | None = None, limit: int = 20,
               partial_limit: int = 5, _: str = Depends(require("reader"))):
        return svc.search(q, topic=topic, limit=limit, partial_limit=partial_limit)

    @r.get("/pages/{path:path}")
    def show(path: str, download: int = 0, _: str = Depends(require("reader"))):
        filename = Path(path).name
        try:
            text = svc.show(path)
        except ValueError as e:
            if "cannot display binary file" in str(e):
                data = svc.read_bytes(path)
                return Response(
                    content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            raise
        headers = (
            {"Content-Disposition": f'attachment; filename="{filename}"'}
            if download else {}
        )
        return Response(content=text, media_type="text/markdown", headers=headers)

    @r.get("/status")
    def status(_: str = Depends(require("reader"))):
        return svc.status()

    @r.get("/log")
    def log(last: int | None = None, _: str = Depends(require("reader"))):
        return {"entries": svc.log(last=last)}

    @r.get("/lint")
    def lint(_: str = Depends(require("reader"))):
        return {"issues": svc.lint()}

    @r.post("/context")
    def context(body: ContextRequest, _: str = Depends(require("reader"))):
        return {"block": svc.context(body.prompt)}

    return r
