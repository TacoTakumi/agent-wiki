"""The viewer's front door and the user's own page: the sign-in page,
password sign-in, sign-out and the user page.

The sign-in page is the one viewer page a browser reaches without a session.
A password sign-in opens a session and sets its cookie (see viewer.session);
sign-out ends the session and clears the cookie. The user page, at /user,
holds the theme choice, the signed-in user's own tokens and, for a session
opened with a password, the change-password form.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response

from agent_wiki.server import accounts
from agent_wiki.server.access import ROLES, WILDCARD
from agent_wiki.server.store import Token, open_store
from agent_wiki.viewer.catalog import VaultCatalog
from agent_wiki.viewer.routes import (readable_catalog, render,
                                      request_target, same_site_path,
                                      templates)
from agent_wiki.viewer.session import (ProxyUserUnknown, SignInRequired,
                                       Viewer, clear_session_cookie,
                                       csrf_refused, current_viewer,
                                       request_is_https,
                                       set_session_cookie,
                                       skip_cookie_refresh)

SIGNIN_PATH = "/signin"
SIGNOUT_PATH = "/signout"
USER_PATH = "/user"
TOKENS_SECTION = USER_PATH + "#tokens"
# How a session opened by password sign-in is marked in the store.
PASSWORD_METHOD = "password"

# One message for every refused sign-in: an unknown user, a user with no
# password and a wrong password read the same.
SIGNIN_FAILED = "That user name and password do not sign in."
# A sign-in post must carry the token its form was served with, and the
# same token comes back in a cookie only this site's sign-in page can set:
# a form on another site can do neither, so it cannot sign a browser in to
# an account of the attacker's choosing.
SIGNIN_COOKIE = "awiki_signin"
SIGNIN_FIELD = "signin"
SIGNIN_TOKEN_TTL = 3600
SIGNIN_FORM_STALE = "That sign-in form has expired. Sign in again."
# What the sign-in page says while the store holds no user.
NO_USERS = (
    "No user exists yet, so nobody can sign in. On the server host, add one"
    " with awiki user add NAME --password and give it a role with"
    " awiki user grant NAME '*' reader."
)


def signin_token_matches(cookie: str | None, field: str | None) -> bool:
    """Whether a sign-in post's field matches its sign-in cookie, compared
    in constant time; a missing or empty either side never matches."""
    if not cookie or not field:
        return False
    return secrets.compare_digest(cookie.encode("utf-8", "replace"),
                                  field.encode("utf-8", "replace"))


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


# --- the user page ------------------------------------------------------------
#
# Every form on the user page, except the theme switch, posts to USER_PATH
# with an `op` field naming what it does, so the page a token create answers
# with stays at the page's own address.

ADD_TOKEN_OP = "add_token"
REVOKE_TOKEN_OP = "revoke_token"
CHANGE_PASSWORD_OP = "change_password"
# The create form's scope choice: no scope (the owner's grants as they are),
# or the VAULT=ROLE entries picked in the rows.
SCOPE_GRANTS = "grants"
SCOPE_ENTRIES = "entries"

SHOWN_ONCE = (
    "Copy the secret now: this page shows it once, and the server keeps only"
    " its hash."
)
NO_TOKENS = "You have no tokens."
NAME_NEEDED = "The token needs a name."
NO_SCOPE_PICKED = (
    "Pick a role for at least one row, or choose no scope to use all your"
    " grants."
)
NO_SUCH_ACTION = "No such action."
PASSWORDS_DIFFER = "The two new passwords do not match."
PASSWORD_CHANGED = (
    "Your password is changed. Every other session of yours has ended."
)


def form_text(form, key: str) -> str:
    """The text field `key` of a parsed form; empty when missing or a file."""
    value = form.get(key)
    return value if isinstance(value, str) else ""


def scope_text(scope: dict[str, str] | None) -> str:
    """A token's scope as the token list shows it."""
    if scope is None:
        return "all my grants"
    return ", ".join(f"{vault}={role}" for vault, role in scope.items())


def token_row(token: Token) -> dict:
    """One token as the token list shows it; never the secret or its hash.
    A token made before the store kept a tail shows 'legacy'."""
    return {
        "name": token.name,
        "last4": token.last4 or "legacy",
        "scope": scope_text(token.scope),
        "created": token.created,
        "last_used": token.last_used or "never",
    }


def build_auth_router(store_file: Path,
                      catalog: VaultCatalog | None = None) -> APIRouter:
    """The sign-in, sign-out and user page routes over the user store at
    `store_file`. The user page's scope rows offer the vaults of `catalog`
    the signed-in user can read."""
    catalog = catalog or VaultCatalog()
    r = APIRouter(include_in_schema=False)

    def sign_in_form(request: Request, next: str, *, status_code: int = 200,
                     error: str | None = None) -> Response:
        # The page never echoes the name tried: every refusal reads the
        # same, byte for byte.
        with open_store(store_file) as store:
            no_users = not store.list_users()
        # A browser that already holds a sign-in cookie keeps it, so a
        # refused try answers with no cookie at all.
        held = request.cookies.get(SIGNIN_COOKIE)
        token = held or secrets.token_urlsafe(32)
        response = render(request, "signin.html", title="Sign in",
                          header_search=False, status_code=status_code,
                          error=error, no_users=NO_USERS if no_users else None,
                          next=same_site_path(next), signin_token=token)
        if not held:
            response.set_cookie(SIGNIN_COOKIE, token, max_age=SIGNIN_TOKEN_TTL,
                                path=SIGNIN_PATH, httponly=True,
                                samesite="strict",
                                secure=request_is_https(request))
        return response

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
                next: str = Form(default="/"),
                signin: str = Form(default="")):
        # Checked before any password work: a refused post costs no scrypt
        # run and records no failure.
        if not signin_token_matches(request.cookies.get(SIGNIN_COOKIE),
                                    signin):
            return sign_in_form(request, next, status_code=403,
                                error=SIGNIN_FORM_STALE)
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

    def user_page(request: Request, viewer: Viewer, *,
                  minted: tuple[str, str] | None = None,
                  status_code: int = 200, **context) -> Response:
        """The user page of `viewer`. `minted` is a new token's (name,
        secret), shown this once: the response that carries it is never
        cached, so a back button or a shared cache cannot show it again."""
        with open_store(store_file) as store:
            tokens = [token_row(t)
                      for t in accounts.list_tokens(store, viewer.name)]
        visible = readable_catalog(request, catalog)
        vaults = list(visible.vaults) + list(visible.unavailable)
        response = render(
            request, "user.html", title="User settings", status_code=status_code,
            user=viewer.name, tokens=tokens, minted=minted,
            shown_once=SHOWN_ONCE, no_tokens=NO_TOKENS, user_path=USER_PATH,
            add_token_op=ADD_TOKEN_OP, revoke_token_op=REVOKE_TOKEN_OP,
            change_password_op=CHANGE_PASSWORD_OP,
            password_session=viewer.method == PASSWORD_METHOD,
            scope_grants=SCOPE_GRANTS, scope_entries=SCOPE_ENTRIES,
            scope_rows=[WILDCARD] + vaults, roles=ROLES, **context)
        if minted is not None:
            response.headers["Cache-Control"] = "no-store"
        return response

    def create_token(request: Request, viewer: Viewer, form) -> Response:
        """Mint a token for the signed-in user and show its secret, once.

        The page answers the post itself rather than redirecting, so the
        secret is in this one response and no later page has it: the store
        keeps only its hash. A blank name or a bad scope is a 400 page; a
        name the user already has is a 409 page.
        """
        name = form_text(form, "name").strip()
        if not name:
            return user_page(request, viewer, status_code=400,
                             create_error=NAME_NEEDED)
        scope = None
        if form_text(form, "scope_mode") == SCOPE_ENTRIES:
            # Each row's select posts VAULT=ROLE, or nothing for a row
            # left out of the token.
            entries = [item for item in form.getlist("scope")
                       if isinstance(item, str) and item]
            try:
                scope = accounts.parse_scope(entries)
            except accounts.AccountError as exc:
                return user_page(request, viewer, status_code=400,
                                 create_error=str(exc))
            if scope is None:
                return user_page(request, viewer, status_code=400,
                                 create_error=NO_SCOPE_PICKED)
        try:
            with open_store(store_file) as store:
                secret = accounts.mint_token(store, viewer.name, name, scope)
        except accounts.AccountError as exc:
            return user_page(request, viewer, status_code=409,
                             create_error=str(exc))
        return user_page(request, viewer, minted=(name, secret))

    def revoke_token(request: Request, viewer: Viewer, form) -> Response:
        """Revoke the signed-in user's token by the name the form carries,
        then back to the token list. Only the user's own tokens are looked
        at, so a name only another user's token carries is not found: a 404
        page, and that token stays as it was."""
        name = form_text(form, "name")
        try:
            with open_store(store_file) as store:
                accounts.revoke_token(store, viewer.name, name)
        except accounts.AccountError as exc:
            return user_page(request, viewer, status_code=404,
                             revoke_error=str(exc))
        return RedirectResponse(TOKENS_SECTION, status_code=303)

    def change_password(request: Request, viewer: Viewer, form) -> Response:
        """Change the signed-in user's password, keeping this session and
        ending their others.

        Only a session opened with a password has the form; a post from a
        proxy session gets a 404 page, as for an op that does not exist. A
        new password that is empty or not typed the same twice, and a wrong
        current password, are refused beside the form with a 400, and
        nothing changes. A wrong current password is no failed sign-in: it
        does not count toward the lockout.
        """
        if viewer.method != PASSWORD_METHOD:
            return user_page(request, viewer, status_code=404,
                             error=NO_SUCH_ACTION)
        new = form_text(form, "new")
        if not new:
            return user_page(request, viewer, status_code=400,
                             password_error=accounts.PASSWORD_EMPTY)
        if new != form_text(form, "again"):
            return user_page(request, viewer, status_code=400,
                             password_error=PASSWORDS_DIFFER)
        with open_store(store_file) as store:
            refusal = accounts.change_password(
                store, viewer.name, form_text(form, "current"), new,
                keep_session_hash=accounts.hash_secret(viewer.secret))
        if refusal is not None:
            return user_page(request, viewer, status_code=400,
                             password_error=refusal)
        return user_page(request, viewer, password_changed=PASSWORD_CHANGED)

    # What each op a user page form posts does. Each runs in the thread
    # pool, so a scrypt run waits off the event loop.
    user_ops = {
        ADD_TOKEN_OP: create_token,
        REVOKE_TOKEN_OP: revoke_token,
        CHANGE_PASSWORD_OP: change_password,
    }

    @r.get(USER_PATH)
    def show_user_page(request: Request,
                       viewer: Viewer = Depends(current_viewer)):
        # The session guard reads the cookie (or a trusted proxy's header)
        # only: a bearer header alone signs nobody in, so it gets the
        # sign-in page.
        return user_page(request, viewer)

    @r.post(USER_PATH)
    async def user_page_form(request: Request,
                             viewer: Viewer = Depends(current_viewer)):
        """Run the user page form the post's op names; a 400 page for an
        op it does not know. The session field is checked first, so a
        refused post changes nothing."""
        form = await request.form()
        refused = csrf_refused(request, form_text(form, "session"))
        if refused is not None:
            return refused
        action = user_ops.get(form_text(form, "op"))
        if action is None:
            return user_page(request, viewer, status_code=400,
                             error=NO_SUCH_ACTION)
        return await run_in_threadpool(action, request, viewer, form)

    return r
