"""Bearer-token auth dependency factory, bound to the served vault's name.

A token is looked up in the user store on every request, and the role it
acts with is the effective role of its owner's grants, narrowed by its
scope, on the served vault. Nothing is cached: a grant change, a revoke or a
user removal made through the CLI applies at the next request.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Header, HTTPException

from agent_wiki.server import accounts
from agent_wiki.server.access import at_least, effective_role
from agent_wiki.server.store import open_store


def make_require(vault_name: str, store_file: Path | None = None):
    """Return a dependency factory: require(min_role) -> FastAPI dependency.

    ``store_file`` is the user store to read (default: the store in the
    awiki config dir, found at each request)."""
    def require(min_role: str):
        def dependency(authorization: str | None = Header(default=None)) -> str:
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="missing bearer token")
            secret = authorization[len("Bearer "):].strip()
            with open_store(store_file) as store:
                token = accounts.token_for(store, secret)
                if token is None:
                    raise HTTPException(status_code=401, detail="invalid token")
                grants = store.grants(token.owner)
            try:
                role = effective_role(grants, token.scope, vault_name)
            except ValueError:
                # A role the ladder does not know grants nothing.
                role = "none"
            if not at_least(role, min_role):
                raise HTTPException(status_code=403, detail="insufficient role")
            return role
        return dependency
    return require
