"""Server config (server.yaml) on the SERVER host: bind, port and proxy sign-in.

``auth_proxy`` says whether the server takes the signed-in user's name from a
header a reverse proxy sets, ``auth_proxy_header`` names that header, and
``auth_trusted_proxies`` lists the peers (addresses or CIDR ranges) trusted to
set it. Any other peer's header names no one.

An older server.yaml also holds a ``tokens`` list (name, role, SHA-256 of the
secret). Tokens now live in the user store; ``awiki user token migrate``
moves that list there and drops the key, and ``awiki serve`` will not start
while it holds entries.

Lives under the config dir (respects AGENT_WIKI_CONFIG_DIR), NOT in the vault —
the vault may be version-controlled/synced and must never carry token hashes.
"""
from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_wiki.config import get_config_dir

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8731

AUTH_PROXY = "auth_proxy"
AUTH_PROXY_HEADER = "auth_proxy_header"
AUTH_TRUSTED_PROXIES = "auth_trusted_proxies"
DEFAULT_AUTH_PROXY_HEADER = "X-WEBAUTH-USER"
DEFAULT_TRUSTED_PROXIES = ("127.0.0.1", "::1")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def server_config_path() -> Path:
    return get_config_dir() / "server.yaml"


def load_server_config() -> dict:
    p = server_config_path()
    data = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
    data.setdefault("bind", DEFAULT_BIND)
    data.setdefault("port", DEFAULT_PORT)
    data.setdefault("tokens", [])
    data.setdefault(AUTH_PROXY, False)
    data.setdefault(AUTH_PROXY_HEADER, DEFAULT_AUTH_PROXY_HEADER)
    data.setdefault(AUTH_TRUSTED_PROXIES, list(DEFAULT_TRUSTED_PROXIES))
    return data


def read_server_yaml() -> dict:
    """server.yaml as written, without the defaults; {} when there is none."""
    p = server_config_path()
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p} is not a YAML mapping.")
    return data


def set_server_tokens(entries: list) -> None:
    """Keep only ``entries`` as server.yaml's tokens; none removes the key.

    Every other key stays as written. With no server.yaml and no entries,
    nothing is written.
    """
    data = read_server_yaml()
    if not entries and "tokens" not in data:
        return
    if entries:
        data["tokens"] = list(entries)
    else:
        data.pop("tokens", None)
    save_server_config(data)


def save_server_config(config: dict) -> None:
    p = server_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()



def _trusted_networks(raw: object) -> tuple[Network, ...]:
    """The networks a trusted proxy list names, or a ValueError naming its fault.

    A single address reads as a network of one. The list refuses what would
    let any peer at all name a user: an empty list, ``*``, and a range that
    holds every address (``0.0.0.0/0``, ``::/0``, or the same spelled with a
    netmask). It refuses an entry that is not an address or a range too,
    rather than drop it: the admin meant something by it.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"server.yaml: {AUTH_TRUSTED_PROXIES} must list at least one"
            f" address or CIDR range of the proxy, not {raw!r}.")
    networks = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip() == "*":
            raise ValueError(
                f"server.yaml: {AUTH_TRUSTED_PROXIES} entry '*' trusts every"
                " address; name the proxy's own address or range.")
        try:
            if not isinstance(entry, str):
                raise ValueError
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            raise ValueError(
                f"server.yaml: {AUTH_TRUSTED_PROXIES} entry {entry!r} is not"
                " an address or a CIDR range.") from None
        if network.prefixlen == 0:
            raise ValueError(
                f"server.yaml: {AUTH_TRUSTED_PROXIES} entry {entry!r} trusts"
                " every address; name the proxy's own address or range.")
        networks.append(network)
    return tuple(networks)


@dataclass(frozen=True)
class ProxyAuth:
    """How the server takes a user's name from a reverse proxy: whether it
    does, the header it reads, and the networks whose peers may set it."""

    enabled: bool = False
    header: str = DEFAULT_AUTH_PROXY_HEADER
    trusted: tuple[Network, ...] = tuple(
        ipaddress.ip_network(entry) for entry in DEFAULT_TRUSTED_PROXIES)

    def trusts(self, host: str | None) -> bool:
        """Whether the peer address ``host`` lies inside a trusted network.

        False for no host and for one that is not an IP address.
        """
        if host is None:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        # A dual-stack socket reports an IPv4 peer as ::ffff:a.b.c.d; it is
        # the same peer, so it is checked as its IPv4 address.
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self.trusted)


def proxy_auth(config: dict) -> ProxyAuth:
    """What server.yaml (``config``) says about proxy sign-in.

    A missing key reads as the default: off, ``X-WEBAUTH-USER``, and loopback
    trusted. A bad value does not degrade: a sign-in that silently trusted
    more, or other, peers than the admin wrote is worse than a server that
    does not start, so a bad setting raises a ValueError naming the key.

    The trusted list is read and checked whether or not ``auth_proxy`` is
    on: it also decides whose ``X-Forwarded-Proto`` counts, so a TLS proxy
    on another host gets Secure cookies without proxy sign-in. With
    ``auth_proxy`` off only the header name goes unchecked, as nothing reads
    it.
    """
    enabled = config.get(AUTH_PROXY, False)
    if not isinstance(enabled, bool):
        raise ValueError(
            f"server.yaml: {AUTH_PROXY} must be true or false, not {enabled!r}.")
    trusted = _trusted_networks(
        config.get(AUTH_TRUSTED_PROXIES, list(DEFAULT_TRUSTED_PROXIES)))
    if not enabled:
        return ProxyAuth(trusted=trusted)
    header = config.get(AUTH_PROXY_HEADER, DEFAULT_AUTH_PROXY_HEADER)
    if not isinstance(header, str) or not header.strip():
        raise ValueError(
            f"server.yaml: {AUTH_PROXY_HEADER} must name a header, not {header!r}.")
    header = header.strip()
    if "_" in header:
        raise ValueError(
            f"server.yaml: {AUTH_PROXY_HEADER} {header!r} holds an underscore;"
            " proxies and servers disagree on such names, so a client could"
            " slip one past the proxy. Use a name with hyphens.")
    return ProxyAuth(enabled=True, header=header, trusted=trusted)
