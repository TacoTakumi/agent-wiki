"""Per-vault access model: the role ladder and effective-role resolution.

A user holds a grant map ``{vault_name_or_'*': role}``. A token may also hold
a scope map of the same shape that can only narrow the grant; a browser session
uses the grant alone (scope ``None``). Pure functions, no I/O.
"""
from __future__ import annotations

ROLES = ("none", "reader", "writer", "admin")
WILDCARD = "*"
_RANK = {role: i for i, role in enumerate(ROLES)}


def role_rank(role: str) -> int:
    """Position of ``role`` on the ladder; ValueError on an unknown role."""
    try:
        return _RANK[role]
    except (KeyError, TypeError):
        raise ValueError(f"unknown role: {role!r}") from None


def is_role(value: str) -> bool:
    """True when ``value`` is a known role name."""
    return isinstance(value, str) and value in _RANK


def resolve(entries: dict[str, str] | None, vault: str) -> str:
    """The entry for ``vault``, else the ``'*'`` entry, else ``'none'``."""
    if not entries:
        return "none"
    if vault in entries:
        return entries[vault]
    return entries.get(WILDCARD, "none")


def effective_role(grants: dict[str, str], scope: dict[str, str] | None,
                   vault: str) -> str:
    """The lower of the grant and the scope for ``vault``; no scope caps at admin."""
    granted = resolve(grants, vault)
    allowed = "admin" if scope is None else resolve(scope, vault)
    return min(granted, allowed, key=role_rank)


def at_least(role: str, minimum: str) -> bool:
    """True when ``role`` ranks at or above ``minimum``."""
    return role_rank(role) >= role_rank(minimum)


def readable_vaults(grants: dict[str, str], names) -> list[str]:
    """The names in ``names``, in their order, on which ``grants`` gives at
    least reader. Browser sessions use the grant alone (no token scope), so
    the viewer and every other session-based reader decide here."""
    return [name for name in names
            if at_least(effective_role(grants, None, name), "reader")]
