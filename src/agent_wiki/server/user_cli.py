"""`awiki user`: manage the server's users and their per-vault grants.

Local on the server host: every command opens the auth store in the awiki
config dir. The rules live in :mod:`agent_wiki.server.accounts`; this
module only asks, prints and maps a refusal to a non-zero exit.

A password is only ever typed at a prompt, never given as an option value,
so it lands neither in shell history nor in the process list.
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
    """Manage server users and their vault grants (local on the server host)."""


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
