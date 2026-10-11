"""The viewer's front door: the sign-in page, password sign-in and sign-out.

The sign-in page is the one viewer page a browser reaches without a session.
A password sign-in opens a session and sets its cookie (see viewer.session);
sign-out ends the session and clears the cookie.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from agent_wiki.server import accounts
from agent_wiki.server.store import open_store
from agent_wiki.viewer.routes import (render, request_target, same_site_path,
                                      templates)
from agent_wiki.viewer.session import (ProxyUserUnknown, SignInRequired,
                                       Viewer, clear_session_cookie,
                                       csrf_refused, current_viewer,
                                       request_is_https,
                                       set_session_cookie,
                                       skip_cookie_refresh)

SIGNIN_PATH = "/signin"
SIGNOUT_PATH = "/signout"

# One message for every refused sign-in: an unknown user, a user with no
# password and a wrong password read the same.
SIGNIN_FAILED = "That user name and password do not sign in."
# What the sign-in page says while the store holds no user.
NO_USERS = (
    "No user exists yet, so nobody can sign in. On the server host, add one"
    " with awiki user add NAME --password and give it a role with"
    " awiki user grant NAME '*' reader."
)


def sign_in_redirect(request: Request, exc: SignInRequired) -> RedirectResponse:
    """The answer to a page asked for without a session: 303 to the sign-in
    page. A GET carries the page it asked for as `next`, so the browser
    comes back to it after signing in; `next` is left out when it is the
    home page or not a path on this site."""
    target = SIGNIN_PATH
    if request.method in ("GET", "HEAD"):
        wanted = same_site_path(request_target(request))
        if wanted != "/":
            target = f"{SIGNIN_PATH}?next={quote(wanted, safe='')}"
    return RedirectResponse(target, status_code=303)


# What a trusted proxy that names no user gets: a 403 page with no sign-in
# form, as a password would not help.
PROXY_USER_UNKNOWN = (
    "Your sign-in names the user {name}, but no user by that name exists on"
    " this server. On the server host, an admin can add it with awiki user"
    " add {name} and give it a role with awiki user grant {name} '*' reader."
)
# The page that says so. The name is escaped like any other template value.
PROXY_REFUSED_PAGE = templates.from_string(
    '{% extends "base.html" %}{% block content %}'
    '<h1 class="page-title">Sign-in refused</h1>'
    '<div class="box"><p class="error" role="alert">{{ message }}</p></div>'
    '{% endblock %}'
)


def refuse_unknown_proxy_user(request: Request,
                              exc: ProxyUserUnknown) -> Response:
    """The answer to a trusted proxy naming no user: 403, and no account
    is made."""
    return render(request, PROXY_REFUSED_PAGE, title="Sign-in refused",
                  header_search=False, status_code=403,
                  message=PROXY_USER_UNKNOWN.format(name=exc.name))


def build_auth_router(store_file: Path) -> APIRouter:
    """The sign-in and sign-out routes over the user store at `store_file`."""
    r = APIRouter(include_in_schema=False)

    def sign_in_form(request: Request, next: str, *, status_code: int = 200,
                     error: str | None = None) -> Response:
        # The page never echoes the name tried: every refusal reads the
        # same, byte for byte.
        with open_store(store_file) as store:
            no_users = not store.list_users()
        return render(request, "signin.html", title="Sign in",
                      header_search=False, status_code=status_code,
                      error=error, no_users=NO_USERS if no_users else None,
                      next=same_site_path(next))

    @r.get(SIGNIN_PATH)
    def sign_in_page(request: Request, next: str = "/"):
        # A live session, or a trusted proxy's header, has nothing to sign
        # in here.
        try:
            current_viewer(request)
        except SignInRequired:
            return sign_in_form(request, next)
        return RedirectResponse(same_site_path(next), status_code=303)

    # A plain def, so the scrypt run waits in the thread pool, off the event
    # loop.
    @r.post(SIGNIN_PATH)
    def sign_in(request: Request, name: str = Form(default=""),
                password: str = Form(default=""),
                next: str = Form(default="/")):
        with open_store(store_file) as store:
            user, locked = accounts.check_password(store, name.strip(),
                                                   password)
            if user is not None:
                secret = accounts.open_session(store, user.name, "password")
        if locked:
            return sign_in_form(request, next, status_code=429,
                                error=accounts.SIGNIN_LOCKED)
        if user is None:
            return sign_in_form(request, next, status_code=400,
                                error=SIGNIN_FAILED)
        response = RedirectResponse(same_site_path(next), status_code=303)
        set_session_cookie(response, secret, secure=request_is_https(request))
        return response

    @r.post(SIGNOUT_PATH)
    def sign_out(request: Request, viewer: Viewer = Depends(current_viewer),
                 session: str | None = Form(default=None)):
        # The form echoes the session secret in its `session` field; a post
        # without it ends nothing.
        refused = csrf_refused(request, session)
        if refused is not None:
            return refused
        with open_store(store_file) as store:
            accounts.end_session(store, viewer.secret)
        # This request may have moved the session's expiry; the response
        # clears the cookie, so it must not set it again too.
        skip_cookie_refresh(request)
        response = RedirectResponse(SIGNIN_PATH, status_code=303)
        clear_session_cookie(response)
        return response

    return r
