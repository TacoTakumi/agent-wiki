"""Account logic for awiki serve: user names, users, grants and passwords.

The functions that change accounts take an open store (see
:mod:`agent_wiki.server.store`), so the CLI and the server share one set of
rules: a user name is checked and lowercased here, a password change ends
the user's sessions here, and a grant's role is checked here. A refusal is
an :class:`AccountError` whose message is fit to show the operator.

A password is stored as
``scrypt$N$r$p$salt$hash`` (salt and hash in hex), so each stored string
names the parameters it was made with: verifying uses those parameters,
and :func:`needs_rehash` tells when they are older than today's.
Verification compares in constant time, and a stored string that asks
scrypt for more than :data:`SCRYPT_MAX_MEMORY` verifies false instead of
allocating it. Stdlib only.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import re
import secrets

from agent_wiki.server.access import ROLES, WILDCARD, is_role
from agent_wiki.server.store import Conflict, User

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 5
SCRYPT_SALT_BYTES = 16
SCRYPT_KEY_BYTES = 32

# The most memory a stored hash may make scrypt use. A stored string is
# data; one that names a huge N must not be able to exhaust the server.
SCRYPT_MAX_MEMORY = 256 * 1024 * 1024

# A user name: 1 to 64 characters from a-z 0-9 . _ - @, starting with a
# letter or digit. Short and plain, so it is safe in a URL, a log line and a
# listing without quoting.
USER_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._@-]{0,63}")
USER_NAME_RULE = (
    "a user name is 1 to 64 characters from a-z 0-9 . _ - @,"
    " starting with a letter or digit"
)


class AccountError(ValueError):
    """An account change refused: a bad name or role, a taken or unknown user."""


def normalise_user_name(name: str) -> str:
    """``name`` lowercased, or an :class:`AccountError` naming the rule it breaks.

    The check runs on the lowercased name, so 'Ann' is kept as 'ann'.
    """
    lowered = name.lower() if isinstance(name, str) else ""
    if not USER_NAME_PATTERN.fullmatch(lowered):
        raise AccountError(f"Bad user name {name!r}: {USER_NAME_RULE}.")
    return lowered


def check_new_user(store, name: str) -> str:
    """The name a new user would be stored under, or a refusal.

    Refuses a bad name and a name already taken, so a caller can check
    before it asks for a password.
    """
    name = normalise_user_name(name)
    if store.get_user(name) is not None:
        raise AccountError(f"A user named {name!r} already exists.")
    return name


def add_user(store, name: str, password: str | None, *, now: str | None = None) -> User:
    """Add the user ``name``; the user as stored.

    The name is stored lowercase. With ``password`` only its scrypt hash is
    kept; with None the user has no password and can sign in only through
    the reverse proxy. An empty password is refused, as is anything
    :func:`check_new_user` refuses.
    """
    name = check_new_user(store, name)
    _refuse_empty(password)
    user = User(
        name=name,
        password_hash=hash_password(password) if password is not None else None,
        created=now or _now(),
    )
    try:
        store.add_user(user)
    except Conflict:
        raise AccountError(f"A user named {name!r} already exists.") from None
    return user


def require_user(store, name: str) -> User:
    """The user called ``name``, in any case, or an :class:`AccountError`."""
    user = store.get_user(name.lower())
    if user is None:
        raise AccountError(f"No user named {name.lower()!r}.")
    return user


def set_password(store, name: str, password: str | None) -> User:
    """Replace the password of ``name``, or clear it with None; the updated user.

    Every session of the user ends with it, so a browser signed in under
    the old password, or before the account went proxy-only, must sign in
    again; the user's failed sign-ins go too, so a lockout ends at once.
    An empty password and an unknown user are refused.
    """
    _refuse_empty(password)
    name = require_user(store, name).name
    password_hash = hash_password(password) if password is not None else None
    # The password first, then the sessions: a session opened under the old
    # password just before the change is still among those dropped.
    user = store.set_user_password(name, password_hash)
    store.remove_user_sessions(name)
    store.clear_signin_failures(name)
    if user is None:
        raise AccountError(f"No user named {name!r}.")
    return user


def clear_password(store, name: str) -> User:
    """Remove the password of ``name``: proxy-only from now on, sessions ended."""
    return set_password(store, name, None)


def remove_user(store, name: str) -> str:
    """Remove ``name`` with their grants, tokens, sessions and failures; the name removed."""
    name = name.lower()
    if not store.remove_user(name):
        raise AccountError(f"No user named {name!r}.")
    return name


def set_grant(store, name: str, vault: str, role: str) -> str:
    """Give ``name`` ``role`` on ``vault`` (or ``'*'``), replacing that entry; the user's name.

    An unknown role or user is refused and the grant map stays as it was.
    """
    if not is_role(role):
        raise AccountError(f"Unknown role {role!r}: expected one of {', '.join(ROLES)}.")
    _refuse_empty_vault(vault)
    name = require_user(store, name).name
    store.set_grant(name, vault, role)
    return name


def remove_grant(store, name: str, vault: str) -> str:
    """Drop the entry for ``vault`` (or ``'*'``) from ``name``'s grants; the user's name."""
    name = require_user(store, name).name
    if not store.remove_grant(name, vault):
        raise AccountError(f"{name} has no grant on {vault!r}.")
    return name


def stale_vault_entries(store, vault_names) -> list[tuple[str, str, str]]:
    """Every grant or token scope entry that names a vault not in ``vault_names``.

    Each entry is ``(user, where, vault)``, where ``where`` is ``'grant'`` or
    ``'token <name>'``; ordered by user, grants before tokens, then by
    vault. ``'*'`` names every vault, so it is never stale.
    """
    known = set(vault_names) | {WILDCARD}
    tokens: dict[str, list] = {}
    for token in store.list_tokens():
        tokens.setdefault(token.owner, []).append(token)
    entries = []
    for user in store.list_users():
        for vault in store.grants(user.name):
            if vault not in known:
                entries.append((user.name, "grant", vault))
        for token in tokens.get(user.name, []):
            for vault in sorted(token.scope or {}):
                if vault not in known:
                    entries.append((user.name, f"token {token.name}", vault))
    return entries


def _refuse_empty(password: str | None) -> None:
    if password is not None and not password:
        raise AccountError("A password may not be empty.")


def _refuse_empty_vault(vault: str) -> None:
    if not vault:
        raise AccountError(f"A grant names a vault or {WILDCARD!r}; got an empty name.")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_password(
    password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P
) -> str:
    """The stored form of ``password``: a scrypt hash under a fresh random salt.

    ``n``, ``r`` and ``p`` default to today's parameters; tests pass other
    values to make hashes that :func:`needs_rehash` reports as stale.
    """
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = _scrypt(password, salt, n, r, p, SCRYPT_KEY_BYTES)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` hashes to ``stored``, under the parameters ``stored`` names.

    A malformed stored string, or one asking for more memory than
    :data:`SCRYPT_MAX_MEMORY`, verifies false rather than raising.
    """
    parsed = _parse(stored)
    if parsed is None:
        return False
    n, r, p, salt, expected = parsed
    try:
        digest = _scrypt(password, salt, n, r, p, len(expected))
    except (ValueError, OverflowError, MemoryError):
        return False
    return hmac.compare_digest(digest, expected)


def needs_rehash(stored: str) -> bool:
    """Whether ``stored`` was made under other parameters than today's, or is unreadable."""
    parsed = _parse(stored)
    if parsed is None:
        return True
    n, r, p, _salt, expected = parsed
    return (n, r, p, len(expected)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_KEY_BYTES)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    # scrypt needs about 128 * r * (N + p) bytes; past the cap, refuse
    # before asking the library for it.
    needed = 128 * r * (n + p + 2)
    if needed > SCRYPT_MAX_MEMORY:
        raise ValueError("scrypt parameters ask for too much memory")
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=needed + 1024 * 1024
    )


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    """The parameters, salt and hash a stored string names, or None if it is malformed."""
    parts = stored.split("$") if isinstance(stored, str) else []
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = (int(part) for part in parts[1:4])
        salt, digest = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
    except ValueError:
        return None
    if n < 2 or n & (n - 1) or r < 1 or p < 1 or not salt or not digest:
        return None
    return n, r, p, salt, digest


# --- browser sessions --------------------------------------------------------
#
# A session is a random cookie secret. The store keeps only its SHA-256, so a
# copy of the store does not hand out live sessions. A session expires
# SESSION_TTL after its last use; a use moves the expiry, but the row (and
# so the cookie) is rewritten at most once per SESSION_WRITE_EVERY, so a
# page that fires many requests a second writes once.

import time  # noqa: E402

from agent_wiki.server.store import Session, SqliteStore  # noqa: E402

SESSION_TTL = 12 * 3600
SESSION_WRITE_EVERY = 60


def hash_secret(secret: str) -> str:
    """The stored form of a session or token secret: its SHA-256 hex digest."""
    return hashlib.sha256(secret.encode()).hexdigest()


def open_session(
    store: SqliteStore, user: str, method: str, *, now: float | None = None
) -> str:
    """Start a session for ``user`` and return its cookie secret; the only copy.

    The store keeps the secret's hash, the user, how the session was opened
    (one of the store's ``SESSION_METHODS``) and an expiry
    :data:`SESSION_TTL` after ``now``. Sessions already expired are swept
    on the way. ValueError on an unknown method or user.
    """
    now = time.time() if now is None else now
    secret = secrets.token_urlsafe(32)
    store.remove_expired_sessions(now)
    store.add_session(Session(
        hash=hash_secret(secret), user=user, method=method,
        expires=now + SESSION_TTL, written=now,
    ))
    return secret


def resume_session(
    store: SqliteStore, secret: str, *, now: float | None = None
) -> tuple[Session | None, bool]:
    """The live session ``secret`` opens, and whether its expiry was written.

    ``(None, False)`` for an empty or unknown secret, or a session whose
    expiry is at or before ``now``; an expired session's row is deleted on
    sight. A live session's expiry moves to :data:`SESSION_TTL` after
    ``now``, but only once :data:`SESSION_WRITE_EVERY` has passed since its
    row was last written; the bool is True when that write happened, which
    tells the caller to refresh the cookie.
    """
    if not secret:
        return None, False
    now = time.time() if now is None else now
    digest = hash_secret(secret)
    # The lookup is by hash: equal hashes mean equal secrets, and the
    # secret itself is never compared, so there is no timing to leak.
    session = store.get_session(digest)
    if session is None:
        return None, False
    if session.expires <= now:
        store.remove_session(digest)
        return None, False
    if now - session.written < SESSION_WRITE_EVERY:
        return session, False
    updated = store.update_session(digest, expires=now + SESSION_TTL, written=now)
    if updated is None:
        # Ended between the read and the write.
        return None, False
    return updated, True


def end_session(store: SqliteStore, secret: str) -> bool:
    """End the session ``secret`` opens; False when there was none."""
    if not secret:
        return False
    return store.remove_session(hash_secret(secret))
