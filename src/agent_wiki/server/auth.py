"""Bearer-token auth dependency factory, bound to a server config dict."""
from __future__ import annotations

from fastapi import Header, HTTPException

from agent_wiki.server_config import role_for_token, role_rank


def make_require(config: dict):
    """Return a dependency factory: require(min_role) -> FastAPI dependency."""
    def require(min_role: str):
        def dependency(authorization: str | None = Header(default=None)) -> str:
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="missing bearer token")
            token = authorization[len("Bearer "):].strip()
            role = role_for_token(token, config)
            if role is None:
                raise HTTPException(status_code=401, detail="invalid token")
            if role_rank(role) < role_rank(min_role):
                raise HTTPException(status_code=403, detail="insufficient role")
            return role
        return dependency
    return require
