"""`awiki user`: manage the server's users, their per-vault grants and tokens.

Local on the server host: every command opens the auth store in the awiki
config dir. The rules live in :mod:`agent_wiki.server.accounts`; this
module only asks, prints and maps a refusal to a non-zero exit.

A password is only ever typed at a prompt, never given as an option value,
so it lands neither in shell history nor in the process list. A token's
secret is printed once, when it is made, and never again.
"""
from __future__ import annotations

import click

from agent_wiki.server import accounts
from agent_wiki.server.access import ROLES, WILDCARD
from agent_wiki.server.store import open_store

STALE = "(stale)"


def _fail(message: str) -> None:
    raise click.ClickException(message)


def _ask_password() -> str:
    """A password typed twice at the terminal, or a refusal.

    Two different entries or an empty one fail at once instead of asking
    again. On stderr, so stdout keeps to what the command prints.
    """
    first = click.prompt("Password", hide_input=True, default="",
                         show_default=False, err=True)
    if not first:
        _fail("A password may not be empty.")
    second = click.prompt("Repeat password", hide_input=True, default="",
                          show_default=False, err=True)
    if first != second:
        _fail("The two passwords do not match; nothing was changed.")
    return first


def _registry_vault_names() -> set:
    from agent_wiki.config import load_registry
    return set(load_registry())


@click.group("user")
def user_group():
    """Manage server users, their vault grants and tokens (local on the server host)."""


@user_group.command("add")
@click.argument("name")
@click.option("--password", is_flag=True,
              help="Ask for a password at the terminal, twice. Without it the user "
                   "has no password and can sign in only through the reverse proxy.")
def user_add(name, password):
    """Add the user NAME (1 to 64 of a-z 0-9 . _ - @, stored lowercase)."""
    with open_store() as store:
        try:
            # Check before asking, so nobody types a password for a user who
            # cannot be added.
            name = accounts.check_new_user(store, name)
            secret = _ask_password() if password else None
            user = accounts.add_user(store, name, secret)
        except accounts.AccountError as exc:
            _fail(str(exc))
    kind = "with a password" if user.password_hash else "proxy-only, no password"
    click.echo(f"Added user {user.name} ({kind}).")


@user_group.command("list")
def user_list():
    """List users: password set, grants and token count; mark stale vault names."""
    vault_names = _registry_vault_names()
    with open_store() as store:
        users = store.list_users()
        if not users:
            click.echo("No users yet. 'awiki user add <name>' adds the first.")
            return
        stale = {(user, where, vault)
                 for user, where, vault in accounts.stale_vault_entries(store, vault_names)}
        any_stale = bool(stale)
        for user in users:
            tokens = store.list_tokens(user.name)
            password = "password set" if user.password_hash else "no password"
            count = f"{len(tokens)} token" + ("" if len(tokens) == 1 else "s")
            click.echo(f"{user.name}  {password}, {count}")
            grants = store.grants(user.name)
            if not grants:
                click.echo("  no grants")
            for vault, role in grants.items():
                mark = f"  {STALE}" if (user.name, "grant", vault) in stale else ""
                click.echo(f"  grant {vault} {role}{mark}")
            for token in tokens:
                for vault, role in sorted((token.scope or {}).items()):
                    if (user.name, f"token {token.name}", vault) in stale:
                        click.echo(f"  token {token.name} scope {vault} {role}  {STALE}")
    if any_stale:
        click.echo(f"{STALE} names a vault that is not in the registry.")


@user_group.command("passwd")
@click.argument("name")
@click.option("--clear", is_flag=True,
              help="Remove the password instead of asking for one: the user "
                   "becomes proxy-only.")
def user_passwd(name, clear):
    """Set a new password for NAME, asked twice; end their sessions."""
    with open_store() as store:
        try:
            # Check before asking, so nobody types a password for no user.
            name = accounts.require_user(store, name).name
            if clear:
                user = accounts.clear_password(store, name)
            else:
                user = accounts.set_password(store, name, _ask_password())
        except accounts.AccountError as exc:
            _fail(str(exc))
    if user.password_hash:
        click.echo(f"Set a new password for {user.name}; their sessions have ended.")
    else:
        click.echo(f"Cleared the password of {user.name}: proxy-only now; "
                   "their sessions have ended.")


@user_group.command("remove")
@click.argument("name")
def user_remove(name):
    """Remove NAME with every grant, token and session they hold."""
    with open_store() as store:
        try:
            removed = accounts.remove_user(store, name)
        except accounts.AccountError as exc:
            _fail(str(exc))
    click.echo(f"Removed user {removed} with their grants, tokens and sessions.")


@user_group.command("grant")
@click.argument("name")
@click.argument("vault", metavar=f"VAULT|{WILDCARD}")
@click.argument("role", metavar="|".join(ROLES))
def user_grant(name, vault, role):
    """Give NAME a role on VAULT ('*' for every vault), replacing that entry."""
    with open_store() as store:
        try:
            name = accounts.set_grant(store, name, vault, role)
        except accounts.AccountError as exc:
            _fail(str(exc))
    click.echo(f"Granted {name} {role} on {vault}.")


@user_group.command("ungrant")
@click.argument("name")
@click.argument("vault", metavar=f"VAULT|{WILDCARD}")
def user_ungrant(name, vault):
    """Remove the entry for VAULT ('*' for the wildcard) from NAME's grants."""
    with open_store() as store:
        try:
            name = accounts.remove_grant(store, name, vault)
        except accounts.AccountError as exc:
            _fail(str(exc))
    click.echo(f"Removed the grant of {name} on {vault}.")


@user_group.group("token")
def user_token_group():
    """Manage a user's bearer tokens: add, list, revoke and migrate."""


@user_token_group.command("add")
@click.argument("user")
@click.argument("name")
@click.option("--scope", "scopes", multiple=True, metavar="VAULT=ROLE",
              help="Narrow the token to ROLE on VAULT ('*' for every vault); "
                   "repeat for more vaults. Without it the token acts under "
                   "all of the owner's grants.")
def user_token_add(user, name, scopes):
    """Make the token NAME for USER and print its secret once."""
    with open_store() as store:
        try:
            scope = accounts.parse_scope(list(scopes))
            secret = accounts.mint_token(store, user, name, scope)
        except accounts.AccountError as exc:
            _fail(str(exc))
    owner = user.lower()
    click.echo(f"Token {name!r} for {owner} ({_describe_scope(scope)}). "
               "Store it now, it will not be shown again:")
    click.echo(secret)


@user_token_group.command("list")
@click.argument("user")
def user_token_list(user):
    """List USER's tokens with last 4, scope and times; never the secret."""
    with open_store() as store:
        try:
            tokens = accounts.list_tokens(store, user)
        except accounts.AccountError as exc:
            _fail(str(exc))
    owner = user.lower()
    if not tokens:
        click.echo(f"{owner} has no tokens. "
                   f"'awiki user token add {owner} <name>' makes one.")
        return
    for token in tokens:
        # A token carried over from server.yaml has no last 4: its secret was
        # never kept.
        tail = f"...{token.last4}" if token.last4 else "legacy"
        click.echo(f"{token.name}  {tail}")
        click.echo(f"  scope: {_describe_scope(token.scope)}")
        click.echo(f"  created: {token.created}")
        click.echo(f"  last used: {token.last_used or 'never'}")


@user_token_group.command("revoke")
@click.argument("user")
@click.argument("name")
def user_token_revoke(user, name):
    """Delete USER's token NAME; it stops working at its next request."""
    with open_store() as store:
        try:
            accounts.revoke_token(store, user, name)
        except accounts.AccountError as exc:
            _fail(str(exc))
    click.echo(f"Revoked the token {name!r} of {user.lower()}.")


@user_token_group.command("migrate")
@click.option("--to", "user", required=True, metavar="USER",
              help="The user who will own the imported tokens.")
def user_token_migrate(user):
    """Move the tokens in server.yaml into the store, owned by USER.

    Each keeps its name and secret and gets the scope '*' with its old role.
    The tokens key then leaves server.yaml; its other keys stay. A token
    whose name USER already uses is skipped and stays in server.yaml.
    """
    from agent_wiki.server_config import (
        read_server_yaml, server_config_path, set_server_tokens,
    )

    path = server_config_path()
    try:
        entries = read_server_yaml().get("tokens")
    except (ValueError, OSError) as exc:
        _fail(f"Cannot read {path}: {exc}")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        _fail(f"The tokens key in {path} is not a list; fix it, then run this again.")
    with open_store() as store:
        try:
            owner = accounts.require_user(store, user).name
            if not entries:
                set_server_tokens([])
                click.echo(f"No tokens in {path}; nothing to import.")
                return
            imported, skipped = accounts.migrate_server_tokens(store, owner, entries)
        except accounts.AccountError as exc:
            _fail(str(exc))
        # Keep each entry the store does not hold, so no secret is lost.
        left = [entry for entry in entries
                if store.find_token(entry["hash"].lower()) is None]
    already = len(entries) - len(imported) - len(skipped)
    if imported:
        click.echo(f"Imported {len(imported)} token(s) for {owner}: {', '.join(imported)}.")
    if already:
        click.echo(f"{already} token(s) were already in the store.")
    for name in skipped:
        click.echo(f"Skipped {name!r}: {owner} already has a token named {name!r}.",
                   err=True)
    set_server_tokens(left)
    if left:
        click.echo(f"Kept {len(left)} token(s) in {path}; awiki serve will not start "
                   f"until they are gone. Revoke or rename the clashing token "
                   f"('awiki user token revoke {owner} <name>') or migrate them "
                   "to another user, then run this again.", err=True)
    else:
        click.echo(f"Removed the tokens key from {path}.")


def _describe_scope(scope) -> str:
    if not scope:
        return "all of the owner's grants"
    return ", ".join(f"{vault}={role}" for vault, role in sorted(scope.items()))
