"""The viewer's browser sessions: the session cookie, the guard every signed-in
page runs, and the middleware that refreshes the cookie.

A session is a random secret the browser holds in the ``awiki_session``
cookie; the user store keeps only its SHA-256 (see
:mod:`agent_wiki.server.accounts`). Every viewer page except the sign-in page
and the favicons depends on :func:`current_viewer`, which reads the cookie and
resumes the session, or raises :class:`SignInRequired`, which the app turns
into a redirect to the sign-in page. With proxy sign-in on, the guard first
reads a trusted reverse proxy's header and signs its user in.

The cookie never authenticates the /v1 API: that guard reads the bearer
header only.
"""
from __future__ import annotations

import dataclasses
import secrets
from pathlib import Path

from fastapi import Request, Response
from fastapi.responses import PlainTextResponse
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection

from agent_wiki.server import accounts
from agent_wiki.server.store import open_store
from agent_wiki.server_config import ProxyAuth

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

    A trusted reverse proxy's header wins over the cookie: naming a user
    other than the live session's, or with no live session, it signs that
    user in (see :func:`proxy_sign_in`). Naming the live session's own
    user, it keeps that session, however it was opened. A header naming no
    user raises :class:`ProxyUserUnknown` before anything changes.
    """
    named = proxy_user_name(request)
    if named is not None:
        proxy_user_exists(request, named)
    viewer = resume_viewer(request)
    if named is not None and (viewer is None or viewer.name != named):
        viewer = proxy_sign_in(request, named, replacing=viewer)
    if viewer is None:
        raise SignInRequired()
    return viewer


# --- reverse-proxy sign-in ---------------------------------------------------
#
# With auth_proxy on in server.yaml, a reverse proxy that has signed the
# browser in already sends the user's name in a header. The header counts
# only from a socket peer inside the trusted networks: any client can send
# it, so from anywhere else it is ignored. It never reaches the /v1 API,
# whose guard reads the bearer header only.

PROXY_METHOD = "proxy"


class ProxyUserUnknown(Exception):
    """A trusted proxy named a user the store does not have."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def proxy_setting(request: HTTPConnection) -> ProxyAuth:
    """The app's reverse-proxy sign-in setting; off when the app has none."""
    return getattr(request.app.state, "proxy_auth", None) or ProxyAuth()


def trusted_peer(request: HTTPConnection) -> bool:
    """Whether the request's socket peer lies inside the trusted networks.

    The peer is ``request.client`` and only that: X-Forwarded-For,
    Forwarded and the like are never read, as any client can send them. A
    missing peer, or one that is not an IP address, is untrusted.
    """
    client = request.client
    return client is not None and proxy_setting(request).trusts(client.host)


def proxy_user_name(request: HTTPConnection) -> str | None:
    """The user name a trusted reverse proxy sent on ``request``,
    lowercased, or None.

    None while proxy sign-in is off and for a peer outside the trusted
    networks. The header is read by its configured name only; an
    underscore spelling of it is another header and is ignored. A header
    sent more than once, or empty, names no one.
    """
    setting = proxy_setting(request)
    if not setting.enabled or not trusted_peer(request):
        return None
    values = request.headers.getlist(setting.header.lower())
    if len(values) != 1:
        return None
    return values[0].strip().lower() or None


def proxy_user_exists(request: HTTPConnection, name: str) -> None:
    """Raise :class:`ProxyUserUnknown` when no user is called ``name``."""
    with open_store(store_file(request)) as store:
        if store.get_user(name) is None:
            raise ProxyUserUnknown(name)


def proxy_sign_in(request: Request, name: str,
                  replacing: Viewer | None = None) -> Viewer:
    """Sign in the user ``name`` a trusted proxy sent, with no form.

    The live session of another user, ``replacing``, ends. A session opened
    with the method 'proxy' goes on the request's state: the response sets
    its cookie whichever route made it, and the forms it carries echo its
    secret. No password is checked. A name no user has raises
    :class:`ProxyUserUnknown` before anything changes: no user is made, no
    session opened and none ended.
    """
    with open_store(store_file(request)) as store:
        user = store.get_user(name)
        if user is None:
            raise ProxyUserUnknown(name)
        if replacing is not None:
            accounts.end_session(store, replacing.secret)
        secret = accounts.open_session(store, user.name, PROXY_METHOD)
    viewer = Viewer(name=user.name, method=PROXY_METHOD, secret=secret)
    setattr(request.state, VIEWER_STATE, viewer)
    setattr(request.state, REFRESH_STATE, secret)
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
    cookie goes out Secure.

    True for a request the socket itself took over https. ``awiki serve``
    reads no forwarded header into the scheme, so behind a reverse proxy
    that ends TLS every request arrives as plain http; one from a peer
    inside the trusted networks that says ``X-Forwarded-Proto: https``
    counts as https too. The trusted list (auth_trusted_proxies) holds
    whether or not proxy sign-in is on. The header's first value
    is read, and from any other peer it counts for nothing, as any client
    can send it.
    """
    if request.url.scheme == "https":
        return True
    if not trusted_peer(request):
        return False
    values = request.headers.getlist("x-forwarded-proto")
    if not values:
        return False
    # A chain of proxies lists one scheme per hop; the first is the browser's.
    return values[0].split(",")[0].strip().lower() == "https"


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


# --- the session field on state-changing forms ---------------------------------
#
# Every viewer form that changes state (sign-out, the theme switch, and the
# user page's forms) carries the session secret in a hidden field named
# ``session`` (render passes it to every signed-in page as ``session``). Its
# POST route runs the session guard first, then, before it changes anything,
#
#     refused = csrf_refused(request, session)
#     if refused is not None:
#         return refused
#
# with ``session: str | None = Form(default=None)`` as a route parameter. A
# post from another site cannot read the secret, so it cannot echo it.

SESSION_FIELD = "session"
CSRF_REFUSED = "That form did not come from this session."


def csrf_refused(request: Request, field_value: str | None) -> Response | None:
    """The 403 answer for a form post whose ``session`` field is missing or
    does not match the caller's session, or None when it matches.

    Without a live session secret every post is refused. The values are
    compared in constant time as bytes: the digest comparison refuses
    non-ASCII text outright, and a hand-built post may carry any text, so a
    wrong value of any shape is a refusal, never a fault.
    """
    expected = session_secret(request)
    posted = (field_value or "").encode("utf-8", errors="replace")
    if expected and secrets.compare_digest(posted, expected.encode("utf-8")):
        return None
    return PlainTextResponse(CSRF_REFUSED, status_code=403)
