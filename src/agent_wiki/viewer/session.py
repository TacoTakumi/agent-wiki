"""The viewer's browser sessions: the session cookie, the guard every signed-in
page runs, and the middleware that refreshes the cookie.

A session is a random secret the browser holds in the ``awiki_session``
cookie; the user store keeps only its SHA-256 (see
:mod:`agent_wiki.server.accounts`). Every viewer page except the sign-in page
and the favicons depends on :func:`current_viewer`, which reads the cookie and
resumes the session, or raises :class:`SignInRequired`, which the app turns
into a redirect to the sign-in page.

The cookie never authenticates the /v1 API: that guard reads the bearer
header only.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from fastapi import Request, Response
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection

from agent_wiki.server import accounts
from agent_wiki.server.store import open_store

SESSION_COOKIE = "awiki_session"

# The request state key that holds the signed-in Viewer, once the guard has
# run, so every page rendered for the request can show who is signed in.
VIEWER_STATE = "viewer"
# The request state key that holds a session secret whose cookie the response
# must set again, because the request moved the session's expiry.
REFRESH_STATE = "session_refresh"


class SignInRequired(Exception):
    """A viewer page was asked for by a browser with no live session."""


@dataclasses.dataclass(frozen=True)
class Viewer:
    """The user signed in on a request: their name, how the session was
    opened ('password' or 'proxy') and the session's secret."""

    name: str
    method: str
    secret: str


def store_file(request: HTTPConnection) -> Path:
    """The user store the app was built with."""
    return request.app.state.store_file


def resume_viewer(request: Request) -> Viewer | None:
    """The user the request's session cookie signs in, or None.

    An expired or unknown session counts as none. When the request moves
    the session's expiry, the response refreshes the cookie (see
    :class:`SessionCookieMiddleware`). The Viewer found is kept on the
    request's state, so a second look in the same request reads no store.
    """
    known = getattr(request.state, VIEWER_STATE, None)
    if known is not None:
        return known
    secret = request.cookies.get(SESSION_COOKIE, "")
    if not secret:
        return None
    with open_store(store_file(request)) as store:
        session, written = accounts.resume_session(store, secret)
    if session is None:
        return None
    viewer = Viewer(name=session.user, method=session.method, secret=secret)
    setattr(request.state, VIEWER_STATE, viewer)
    if written:
        setattr(request.state, REFRESH_STATE, secret)
    return viewer


def current_viewer(request: Request) -> Viewer:
    """The signed-in user, for a page dependency; raises SignInRequired
    without a live session.

    The session cookie is the only way in for now. A sign-in step that
    reads a trusted reverse proxy's header belongs here, before the cookie
    is read, so every page gets it at once.
    """
    viewer = resume_viewer(request)
    if viewer is None:
        raise SignInRequired()
    return viewer


def session_secret(request: Request) -> str:
    """The live session's secret: what a form that changes state echoes back
    in its hidden ``session`` field. Empty without a session."""
    viewer = getattr(request.state, VIEWER_STATE, None)
    if viewer is not None:
        return viewer.secret
    return request.cookies.get(SESSION_COOKIE, "")


def request_is_https(request: HTTPConnection) -> bool:
    """Whether the browser reached the server over https, so the session
    cookie goes out Secure."""
    return request.url.scheme == "https"


def set_session_cookie(response: Response, secret: str, *, secure: bool) -> None:
    """Put the session cookie for ``secret`` on ``response``, for the
    session's full life."""
    response.set_cookie(
        SESSION_COOKIE,
        secret,
        max_age=accounts.SESSION_TTL,
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
    )


def clear_session_cookie(response: Response) -> None:
    """Tell the browser to drop its session cookie."""
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           samesite="lax")


def skip_cookie_refresh(request: Request) -> None:
    """Make the response leave the session cookie alone, as a sign-out does
    when it clears the cookie itself."""
    setattr(request.state, REFRESH_STATE, None)


class SessionCookieMiddleware:
    """Set the session cookie again on a response whose request moved the
    session's expiry.

    The guard found the session and marked the request's state; the cookie
    goes on whatever response the route made. A pure ASGI middleware, so it
    sees the state dict the route wrote to.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # The request's state is this dict; holding it here sees what the
        # route set.
        state = scope.setdefault("state", {})

        async def send_with_cookie(message) -> None:
            secret = state.get(REFRESH_STATE)
            if message["type"] == "http.response.start" and secret:
                cookie = Response()
                set_session_cookie(cookie, secret,
                                   secure=request_is_https(HTTPConnection(scope)))
                MutableHeaders(scope=message).append(
                    "set-cookie", cookie.headers["set-cookie"])
            await send(message)

        await self.app(scope, receive, send_with_cookie)
