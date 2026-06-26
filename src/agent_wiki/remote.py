"""RemoteVaultService: HTTP-backed VaultService over httpx.

Maps HTTP status -> the same exceptions the CLI already handles, so the CLI's
existing `except (ValueError, FileNotFoundError, ClickException)` works for
remote vaults unchanged. The error contract is symmetric with server/app.py.
"""
from __future__ import annotations

from pathlib import Path

import click
import httpx

from agent_wiki.service import VaultService


class RemoteVaultService(VaultService):
    def __init__(self, url: str, token: str | None, client: httpx.Client | None = None):
        self.base = url.rstrip("/")
        self.token = token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._c = client or httpx.Client(base_url=self.base, headers=headers, timeout=30.0)
        if client is not None and token:
            self._c.headers["Authorization"] = f"Bearer {token}"

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code < 400:
            return resp
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        code = resp.status_code
        if code == 404:
            raise FileNotFoundError(detail)
        if code == 409:
            raise FileExistsError(detail)
        if code in (400, 415):
            raise ValueError(detail)
        if code in (401, 403):
            raise click.ClickException(f"server denied request ({code}): {detail}")
        if code == 503:
            raise click.ClickException(f"server busy, try again ({code}): {detail}")
        raise click.ClickException(f"server error ({code}): {detail}")

    # --- reads ---
    def search(self, query, topic=None, limit=20, partial_limit=5) -> dict:
        params = {"q": query, "limit": limit, "partial_limit": partial_limit}
        if topic:
            params["topic"] = topic
        return self._check(self._c.get("/v1/search", params=params)).json()

    def show(self, rel: str) -> str:
        resp = self._c.get(f"/v1/pages/{rel}", params={"download": 0})
        self._check(resp)
        if resp.headers.get("content-type", "").startswith("application/octet-stream"):
            raise ValueError(f"cannot display binary file: {rel}")
        return resp.text

    def read_bytes(self, rel: str) -> bytes:
        return self._check(self._c.get(f"/v1/pages/{rel}", params={"download": 1})).content

    def status(self) -> dict:
        return self._check(self._c.get("/v1/status")).json()

    def log(self, last=None) -> list[str]:
        params = {"last": last} if last is not None else {}
        return self._check(self._c.get("/v1/log", params=params)).json()["entries"]

    def lint(self) -> list[dict]:
        return self._check(self._c.get("/v1/lint")).json()["issues"]

    def context(self, prompt: str) -> str:
        return self._check(self._c.post("/v1/context", json={"prompt": prompt})).json()["block"]

    # --- writes ---
    def ingest(self, source: Path, topic=None, tags=None, update=False, force=False) -> dict:
        return self.ingest_path_bytes(
            Path(source).name, Path(source).read_bytes(),
            topic=topic, tags=tags, update=update, force=force,
        )

    def ingest_path_bytes(self, filename, data, topic=None, tags=None,
                          update=False, force=False) -> dict:
        files = {"file": (filename, data, "application/octet-stream")}
        form = {}
        if topic:
            form["topic"] = topic
        if tags:
            form["tags"] = ",".join(tags)
        if update:
            form["update"] = "true"
        if force:
            form["force"] = "true"
        return self._check(self._c.post("/v1/ingest", files=files, data=form)).json()

    def ingest_url(self, url, topic=None, tags=None, update=False, force=False) -> dict:
        # Client-side fetch + extract (D-17/REQ-09): we ship the extracted markdown
        # plus the original asset to the server, which never fetches. Note the fetch
        # is delegated to fetch_and_extract — no Fetcher is referenced inline here.
        from agent_wiki.ingest import UnchangedURLSkip, fetch_and_extract

        result, extracted = fetch_and_extract(url)
        files = {"asset": ("asset", result.body, "application/octet-stream")}
        form = {
            "source_url": result.source_url,
            "content_type": result.content_type,
            "markdown": extracted.markdown,
        }
        if extracted.title:
            form["title"] = extracted.title
        if topic:
            form["topic"] = topic
        if tags:
            form["tags"] = ",".join(tags)
        if update:
            form["update"] = "true"
        if force:
            form["force"] = "true"
        out = self._check(self._c.post("/v1/ingest_url", files=files, data=form)).json()
        if out.get("unchanged"):
            # Same URL, unchanged body: the server skipped — re-raise so the CLI's
            # existing UnchangedURLSkip handling fires for remote vaults too.
            raise UnchangedURLSkip(out.get("url", url))
        return out

    def reingest(self, name: str, force: bool = False) -> dict:
        return self._check(self._c.post("/v1/reingest", json={
            "name": name, "force": force,
        })).json()

    def ingest_conversation(self, bundle: Path, no_summarize=False) -> dict:
        files = {"file": (Path(bundle).name, Path(bundle).read_bytes(), "text/markdown")}
        form = {"no_summarize": str(no_summarize).lower()}
        return self._check(self._c.post("/v1/conversations", files=files, data=form)).json()

    def rebuild_index(self) -> dict:
        return self._check(self._c.post("/v1/index")).json()

    def sync(self, source=None, since=None, dry_run=False, include_live=False) -> dict:
        return self._check(self._c.post("/v1/sync", json={
            "source": source, "since": since,
            "dry_run": dry_run, "include_live": include_live,
        })).json()

    def adapt(self, source, ref, output=None) -> dict:
        return self._check(self._c.post("/v1/adapt", json={
            "source": source, "ref": ref, "output": output,
        })).json()

    def doctor(self, fix=False, dry_run=False) -> dict:
        return self._check(self._c.post("/v1/doctor", json={
            "fix": fix, "dry_run": dry_run,
        })).json()
