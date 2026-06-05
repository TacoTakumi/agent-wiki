"""HTTP routes. Each handler calls a VaultService method and serializes.
Built against an injected service + auth dependency factory so the same
router works for any vault/config (and is trivially testable)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import Response

from agent_wiki.server.schemas import (
    AdaptRequest, ContextRequest, DoctorRequest, SyncRequest,
)


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

    # NOTE: one endpoint cannot declare BOTH Form/File and a JSON pydantic body
    # (mutually exclusive content types). Branch on Content-Type via Request so
    # the same path accepts multipart OR JSON, per the endpoint map.
    @r.post("/ingest", status_code=201)
    async def ingest(request: Request, _: str = Depends(require("writer"))):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None:
                raise HTTPException(status_code=400, detail="missing 'file' part")
            name, data = upload.filename, await upload.read()
            topic = form.get("topic") or None
            tags = form.get("tags") or None
            update = str(form.get("update") or "").lower() in ("1", "true", "yes")
        elif ctype.startswith("application/json"):
            body = await request.json()
            try:
                name, data = body["filename"], body["content"].encode()
            except (KeyError, TypeError, AttributeError):
                raise HTTPException(status_code=400, detail="JSON needs filename + content")
            topic = body.get("topic")
            tags = body.get("tags")
            update = bool(body.get("update", False))
        else:
            raise HTTPException(status_code=400, detail="send multipart file or JSON body")
        tag_list = [s.strip() for s in tags.split(",")] if tags else None
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / Path(name).name
            tmp.write_bytes(data)
            return svc.ingest(tmp, topic=topic, tags=tag_list, update=update)

    @r.post("/conversations", status_code=201)
    async def conversations(
        file: UploadFile = File(...),
        no_summarize: bool = Form(default=False),
        _: str = Depends(require("writer")),
    ):
        data = await file.read()
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / Path(file.filename).name
            tmp.write_bytes(data)
            return svc.ingest_conversation(tmp, no_summarize=no_summarize)

    @r.post("/index")
    def index(_: str = Depends(require("writer"))):
        return svc.rebuild_index()

    @r.post("/sync")
    def sync(body: SyncRequest, _: str = Depends(require("writer"))):
        return svc.sync(source=body.source, since=body.since,
                        dry_run=body.dry_run, include_live=body.include_live)

    @r.post("/adapt")
    def adapt(body: AdaptRequest, _: str = Depends(require("writer"))):
        return svc.adapt(body.source, body.ref, output=body.output)

    @r.post("/doctor")
    def doctor(body: DoctorRequest, _: str = Depends(require("admin"))):
        return svc.doctor(fix=body.fix, dry_run=body.dry_run)

    return r
