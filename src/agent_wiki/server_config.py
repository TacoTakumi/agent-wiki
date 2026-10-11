"""Server config (server.yaml) on the SERVER host: bind and port.

An older server.yaml also holds a ``tokens`` list (name, role, SHA-256 of the
secret). Tokens now live in the user store; ``awiki user token migrate``
moves that list there and drops the key, and ``awiki serve`` will not start
while it holds entries.

Lives under the config dir (respects AGENT_WIKI_CONFIG_DIR), NOT in the vault —
the vault may be version-controlled/synced and must never carry token hashes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from agent_wiki.config import get_config_dir

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8731
_ROLES = {"reader": 0, "writer": 1, "admin": 2}


def server_config_path() -> Path:
    return get_config_dir() / "server.yaml"


def load_server_config() -> dict:
    p = server_config_path()
    data = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
    data.setdefault("bind", DEFAULT_BIND)
    data.setdefault("port", DEFAULT_PORT)
    data.setdefault("tokens", [])
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


def role_for_token(token: str, config: dict) -> str | None:
    h = hash_token(token)
    for entry in config.get("tokens", []):
        if entry.get("hash") == h:
            return entry.get("role")
    return None


def role_rank(role: str) -> int:
    return _ROLES.get(role, -1)
